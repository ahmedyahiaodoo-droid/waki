from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero


class WakiWarehouse(models.Model):
    _name = 'waki.warehouse'
    _description = 'Waki Warehouse'
    _order = 'name'

    name = fields.Char(required=True)
    code = fields.Char(required=True, size=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Real Warehouse', required=True,
        domain="[('company_id', '=', company_id)]",
        help='Real warehouse whose receipts and manufacturing orders are mirrored here.')
    sync_receipts = fields.Boolean(string='Sync Purchase Receipts', default=True)
    sync_manufacturing = fields.Boolean(string='Sync Manufacturing Orders', default=True)
    allow_negative = fields.Boolean(
        string='Allow Negative Waki Stock', default=True,
        help='If disabled, a waki consumption that would go below zero raises an error '
             'and blocks the real operation.')
    stock_ids = fields.One2many('waki.stock', 'waki_warehouse_id')
    stock_count = fields.Integer(compute='_compute_stock_count')

    _sql_constraints = [
        ('warehouse_uniq', 'unique(warehouse_id)',
         'A real warehouse can be linked to only one waki warehouse.'),
        ('code_company_uniq', 'unique(code, company_id)',
         'The waki warehouse code must be unique per company.'),
    ]

    def _compute_stock_count(self):
        data = dict(self.env['waki.stock']._read_group(
            [('waki_warehouse_id', 'in', self.ids)],
            ['waki_warehouse_id'], ['__count']))
        for rec in self:
            rec.stock_count = data.get(rec, 0)

    @api.model
    def _get_for_warehouse(self, warehouse):
        """Return the Waki warehouse of an Odoo warehouse, creating it if missing
        (including archived ones, so a deliberately archived link stays off)."""
        if not warehouse:
            return self.browse()
        waki_wh = self.with_context(active_test=False).search(
            [('warehouse_id', '=', warehouse.id)], limit=1)
        if not waki_wh:
            waki_wh = self.create(self._prepare_from_warehouse(warehouse))
        return waki_wh if waki_wh.active else self.browse()

    @api.model
    def _prepare_from_warehouse(self, warehouse):
        return {
            'name': f'Waki {warehouse.name}',
            'code': (warehouse.code or 'WH')[:10],
            'company_id': warehouse.company_id.id,
            'warehouse_id': warehouse.id,
        }

    def action_view_stock(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'waki_mrp.action_waki_stock')
        action['domain'] = [('waki_warehouse_id', '=', self.id)]
        action['context'] = {'default_waki_warehouse_id': self.id}
        return action

    def action_initialize_from_real_stock(self):
        """Opening balance: copy on-hand quantities of the real warehouse
        into the waki warehouse as adjustment moves (quantities only)."""
        WakiMove = self.env['waki.move']
        for rec in self:
            if WakiMove.search_count([('waki_warehouse_id', '=', rec.id),
                                        ('state', '=', 'done')], limit=1):
                raise UserError(_(
                    'Waki warehouse "%s" already has posted moves. '
                    'Use manual adjustments instead.', rec.name))
            groups = self.env['stock.quant']._read_group(
                [('location_id', 'child_of', rec.warehouse_id.view_location_id.id),
                 ('location_id.usage', '=', 'internal')],
                ['product_id'], ['quantity:sum'])
            vals_list = []
            for product, qty in groups:
                if float_is_zero(qty, precision_rounding=product.uom_id.rounding):
                    continue
                vals_list.append({
                    'waki_warehouse_id': rec.id,
                    'product_id': product.id,
                    'quantity': abs(qty),
                    'direction': 'in' if float_compare(
                        qty, 0, precision_rounding=product.uom_id.rounding) > 0 else 'out',
                    'move_type': 'adjustment',
                    'origin': _('Opening balance from %s', rec.warehouse_id.name),
                })
            if vals_list:
                WakiMove.create(vals_list).action_done()
        return True
