from odoo import api, fields, models

# Header fields of mrp.production that trigger a Waki sync when written.
# Component fields (move_raw_ids, move_byproduct_ids, workorders...) are
# deliberately NOT here: changes to components never reach the Waki order.
WAKI_HEADER_FIELDS = {
    'origin', 'priority', 'company_id', 'user_id', 'date_start', 'date_finished',
    'date_deadline', 'product_qty', 'product_uom_id', 'qty_producing',
}
WAKI_REBUILD_FIELDS = {'product_id', 'bom_id', 'picking_type_id'}


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    waki_production_ids = fields.One2many('waki.production', 'production_id', copy=False)
    waki_production_count = fields.Integer(compute='_compute_waki_production_count')

    def _compute_waki_production_count(self):
        for mo in self:
            mo.waki_production_count = len(mo.sudo().waki_production_ids)

    # ------------------------------------------------------------------
    # CRUD sync
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        productions = super().create(vals_list)
        productions._waki_sync_production()
        return productions

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get('waki_no_sync'):
            return res
        keys = set(vals)
        if keys & WAKI_REBUILD_FIELDS:
            # Product / BoM / operation type changed while still draft: recreate.
            to_rebuild = self.filtered(lambda p: p.state == 'draft')
            to_rebuild.sudo().waki_production_ids.filtered(
                lambda w: not w.is_posted).with_context(waki_sync_unlink=True).unlink()
            to_rebuild._waki_sync_production()
        if keys & WAKI_HEADER_FIELDS:
            self._waki_push_header()
        return res

    def unlink(self):
        waki = self.sudo().waki_production_ids.filtered(lambda w: not w.is_posted)
        res = super().unlink()
        waki.exists().with_context(waki_sync_unlink=True).unlink()
        return res

    def _waki_sync_production(self):
        WakiProduction = self.env['waki.production'].sudo()
        WakiWarehouse = self.env['waki.warehouse'].sudo()
        WakiBom = self.env['waki.bom'].sudo()
        for mo in self:
            if mo.sudo().waki_production_ids:
                continue
            warehouse = mo.picking_type_id.warehouse_id or self.env['stock.warehouse'].sudo().search(
                [('company_id', '=', mo.company_id.id)], limit=1)
            waki_wh = WakiWarehouse._get_for_warehouse(warehouse)
            if not waki_wh or not waki_wh.sync_manufacturing:
                continue
            waki_bom = WakiBom._get_or_create(mo.product_id, mo.bom_id, mo.company_id)
            WakiProduction.create(WakiProduction._prepare_from_production(mo, waki_wh, waki_bom))

    def _waki_push_header(self):
        """Mirror header data (qty, dates, responsible, priority...) - never components."""
        WakiProduction = self.env['waki.production']
        for mo in self:
            waki = mo.sudo().waki_production_ids.filtered(lambda w: not w.is_posted)
            if waki:
                waki.write(WakiProduction._prepare_header_from_production(mo))

    # ------------------------------------------------------------------
    # Workflow sync (status itself is mirrored by a computed field)
    # ------------------------------------------------------------------
    def action_confirm(self):
        res = super().action_confirm()
        self._waki_sync_production()
        self._waki_push_header()
        return res

    def button_plan(self):
        res = super().button_plan()
        self._waki_push_header()
        return res

    def button_mark_done(self):
        res = super().button_mark_done()
        productions = self | self.procurement_group_id.mrp_production_ids
        productions._waki_sync_production()  # backorders created during closing
        for mo in productions:
            waki = mo.sudo().waki_production_ids
            if mo.state == 'done':
                mo._waki_push_header()
                waki.filtered(lambda w: not w.is_posted)._post_done(mo.qty_produced)
            else:
                mo._waki_push_header()
        return res

    def action_cancel(self):
        res = super().action_cancel()
        self._waki_push_header()
        return res

    def action_waki_sync(self):
        """Manual sync for orders created before the module was installed."""
        self._waki_sync_production()
        self._waki_push_header()
        for mo in self.filtered(lambda p: p.state == 'done'):
            mo.sudo().waki_production_ids.filtered(lambda w: not w.is_posted)._post_done(mo.qty_produced)
        return True

    def action_view_waki_production(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('waki_mrp.action_waki_production')
        waki = self.waki_production_ids
        if len(waki) == 1:
            action.update({'res_id': waki.id, 'view_mode': 'form', 'views': [(False, 'form')]})
        else:
            action['domain'] = [('id', 'in', waki.ids)]
            action['context'] = {}
        return action
