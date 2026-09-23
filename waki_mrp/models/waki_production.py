from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_is_zero, float_round

WAKI_STATES = [
    ('draft', 'Draft'),
    ('confirmed', 'Confirmed'),
    ('progress', 'In Progress'),
    ('to_close', 'To Close'),
    ('done', 'Done'),
    ('cancel', 'Cancelled'),
]


class WakiProduction(models.Model):
    _name = 'waki.production'
    _description = 'Waki Manufacturing Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, date_planned desc, id desc'

    name = fields.Char(string='Reference', default='New', readonly=True, copy=False)
    production_id = fields.Many2one(
        'mrp.production', string='Odoo Manufacturing Order', readonly=True, index=True,
        ondelete='set null', copy=False)
    origin = fields.Char(string='Source', tracking=True)
    priority = fields.Selection([('0', 'Normal'), ('1', 'Urgent')], default='0')
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    user_id = fields.Many2one(
        'res.users', string='Responsible', default=lambda self: self.env.user, tracking=True)
    date_planned = fields.Datetime(string='Scheduled Date', default=fields.Datetime.now, tracking=True)
    date_finished_planned = fields.Datetime(string='Scheduled End')
    date_deadline = fields.Datetime(string='Deadline', tracking=True)
    waki_warehouse_id = fields.Many2one(
        'waki.warehouse', string='Waki Warehouse', required=True,
        domain="[('company_id', '=', company_id)]")
    product_id = fields.Many2one(
        'product.product', string='Product', required=True, domain=[('type', '=', 'consu')],
        tracking=True)
    product_tmpl_id = fields.Many2one(related='product_id.product_tmpl_id', store=True)
    product_uom_category_id = fields.Many2one(related='product_id.uom_id.category_id')
    product_uom_id = fields.Many2one(
        'uom.uom', string='Unit', required=True,
        domain="[('category_id', '=', product_uom_category_id)]")
    product_qty = fields.Float(
        string='Quantity To Produce', default=1.0, digits='Product Unit of Measure',
        required=True, tracking=True)
    qty_producing = fields.Float(
        string='Quantity Producing', digits='Product Unit of Measure', readonly=True)
    qty_produced = fields.Float(
        string='Quantity Produced', digits='Product Unit of Measure', readonly=True, tracking=True)
    waki_bom_id = fields.Many2one(
        'waki.bom', string='Shadow BoM',
        domain="['|', ('product_id', '=', product_id), '&', ('product_id', '=', False), "
               "('product_tmpl_id', '=', product_tmpl_id)]")
    bom_uom_id = fields.Many2one('uom.uom', readonly=True)

    # Linked orders mirror the Odoo MO status; manual orders use manual_state.
    manual_state = fields.Selection(WAKI_STATES, default='draft', copy=False)
    state = fields.Selection(
        WAKI_STATES, compute='_compute_state', store=True, readonly=True,
        tracking=True, index=True)
    is_posted = fields.Boolean(
        string='Waki Stock Posted', readonly=True, copy=False,
        help='Components consumed and finished product received in the Waki warehouse.')
    date_done = fields.Datetime(string='End Date', readonly=True, copy=False)
    line_ids = fields.One2many(
        'waki.production.line', 'waki_production_id', string='Components', copy=False)
    move_ids = fields.One2many('waki.move', 'waki_production_id', readonly=True)
    move_count = fields.Integer(compute='_compute_move_count')

    _sql_constraints = [
        ('qty_positive', 'CHECK(product_qty > 0)', 'The quantity to produce must be positive.'),
    ]

    @api.depends('production_id', 'production_id.state', 'manual_state')
    def _compute_state(self):
        for rec in self:
            rec.state = rec.production_id.state if rec.production_id else rec.manual_state

    def _compute_move_count(self):
        for rec in self:
            rec.move_count = len(rec.move_ids)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.product_uom_id = self.product_id.uom_id
            self.waki_bom_id = self.env['waki.bom']._find_for_product(
                self.product_id, self.company_id)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('waki.production') or 'New'
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Sync helpers (called from mrp.production)
    # ------------------------------------------------------------------
    @api.model
    def _prepare_line_vals(self, waki_bom):
        lines = []
        for bl in waki_bom.line_ids:
            qty = bl.product_uom_id._compute_quantity(
                bl.product_qty, bl.product_id.uom_id, round=False)
            lines.append((0, 0, {
                'product_id': bl.product_id.id,
                'qty_per_unit': qty / waki_bom.product_qty,
            }))
        return lines

    @api.model
    def _prepare_header_from_production(self, production):
        """Everything that is synchronized continuously (never the components)."""
        return {
            'origin': production.name if not production.origin else
                      f'{production.name} ({production.origin})',
            'priority': production.priority,
            'company_id': production.company_id.id,
            'user_id': production.user_id.id,
            'date_planned': production.date_start,
            'date_finished_planned': production.date_finished,
            'date_deadline': production.date_deadline,
            'product_qty': production.product_qty,
            'product_uom_id': production.product_uom_id.id,
            'qty_producing': production.qty_producing,
            'manual_state': production.state,
        }

    @api.model
    def _prepare_from_production(self, production, waki_warehouse, waki_bom):
        vals = self._prepare_header_from_production(production)
        vals.update({
            'production_id': production.id,
            'waki_warehouse_id': waki_warehouse.id,
            'product_id': production.product_id.id,
            'waki_bom_id': waki_bom.id,
            'bom_uom_id': (waki_bom.product_uom_id or production.product_uom_id).id,
            'line_ids': self._prepare_line_vals(waki_bom) if waki_bom else [],
        })
        return vals

    # ------------------------------------------------------------------
    # Manual (unlinked) Waki orders
    # ------------------------------------------------------------------
    def _check_manual(self):
        if any(rec.production_id for rec in self):
            raise UserError(_('This Waki order is synchronized with an Odoo manufacturing order; '
                              'its status follows that order.'))

    def action_confirm(self):
        self._check_manual()
        for rec in self.filtered(lambda r: r.state == 'draft'):
            if not rec.line_ids and rec.waki_bom_id:
                rec.write({
                    'bom_uom_id': rec.waki_bom_id.product_uom_id.id,
                    'line_ids': self._prepare_line_vals(rec.waki_bom_id),
                })
            if not rec.bom_uom_id:
                rec.bom_uom_id = rec.product_uom_id
            rec.manual_state = 'confirmed'
        return True

    def action_mark_done(self):
        self._check_manual()
        self.action_confirm()
        for rec in self:
            rec._post_done(rec.product_qty)
            rec.manual_state = 'done'
        return True

    def action_cancel(self):
        self._check_manual()
        if any(r.state == 'done' for r in self):
            raise UserError(_('A done Waki manufacturing order cannot be cancelled.'))
        self.write({'manual_state': 'cancel'})
        return True

    def action_draft(self):
        self._check_manual()
        self.filtered(lambda r: r.state == 'cancel').write({'manual_state': 'draft'})
        return True

    @api.ondelete(at_uninstall=False)
    def _unlink_except_done_or_linked(self):
        if self.env.context.get('waki_sync_unlink'):
            return
        if any(r.is_posted or r.production_id for r in self):
            raise UserError(_('Done or synchronized Waki manufacturing orders cannot be deleted.'))

    # ------------------------------------------------------------------
    # Waki stock posting: standard BoM only
    # ------------------------------------------------------------------
    def _post_done(self, qty_produced):
        """Consume components strictly by the frozen standard BoM, whatever
        happened to components on the Odoo MO, and receive the finished qty.
        Only the Waki warehouse is affected: no Odoo stock, cost or accounting."""
        WakiMove = self.env['waki.move'].sudo()
        for rec in self.filtered(lambda r: not r.is_posted):
            vals_list = []
            qty_bom_uom = rec.product_uom_id._compute_quantity(
                qty_produced, rec.bom_uom_id or rec.product_uom_id, round=False)
            for line in rec.line_ids:
                rounding = line.product_id.uom_id.rounding
                qty = float_round(line.qty_per_unit * qty_bom_uom, precision_rounding=rounding)
                line.qty_consumed = qty
                if float_is_zero(qty, precision_rounding=rounding):
                    continue
                vals_list.append({
                    'waki_warehouse_id': rec.waki_warehouse_id.id,
                    'product_id': line.product_id.id,
                    'quantity': qty,
                    'direction': 'out',
                    'move_type': 'mo_consume',
                    'origin': rec.name,
                    'waki_production_id': rec.id,
                })
            finished_qty = rec.product_uom_id._compute_quantity(qty_produced, rec.product_id.uom_id)
            if not float_is_zero(finished_qty, precision_rounding=rec.product_id.uom_id.rounding):
                vals_list.append({
                    'waki_warehouse_id': rec.waki_warehouse_id.id,
                    'product_id': rec.product_id.id,
                    'quantity': finished_qty,
                    'direction': 'in',
                    'move_type': 'mo_produce',
                    'origin': rec.name,
                    'waki_production_id': rec.id,
                })
            if vals_list:
                WakiMove.create(vals_list).action_done()
            rec.write({'is_posted': True, 'qty_produced': qty_produced,
                       'date_done': fields.Datetime.now()})
        return True

    def action_view_real_production(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mrp.production',
            'res_id': self.production_id.id,
            'view_mode': 'form',
        }

    def action_view_moves(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('waki_mrp.action_waki_move')
        action['domain'] = [('waki_production_id', '=', self.id)]
        action['context'] = {}
        return action


class WakiProductionLine(models.Model):
    _name = 'waki.production.line'
    _description = 'Waki Manufacturing Order Component'

    waki_production_id = fields.Many2one(
        'waki.production', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', string='Component', required=True)
    uom_id = fields.Many2one(related='product_id.uom_id')
    qty_per_unit = fields.Float(string='Std Qty / Unit', digits=(16, 8))
    qty_expected = fields.Float(
        string='To Consume', digits='Product Unit of Measure',
        compute='_compute_qty_expected', store=True)
    qty_consumed = fields.Float(string='Consumed', digits='Product Unit of Measure', readonly=True)

    @api.depends('qty_per_unit', 'waki_production_id.product_qty',
                 'waki_production_id.product_uom_id', 'waki_production_id.bom_uom_id')
    def _compute_qty_expected(self):
        for line in self:
            prod = line.waki_production_id
            bom_uom = prod.bom_uom_id or prod.product_uom_id
            if not bom_uom or not prod.product_uom_id:
                line.qty_expected = 0.0
                continue
            qty = prod.product_uom_id._compute_quantity(prod.product_qty, bom_uom, round=False)
            line.qty_expected = line.qty_per_unit * qty
