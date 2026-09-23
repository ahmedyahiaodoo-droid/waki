from odoo import _, api, fields, models
from odoo.exceptions import UserError


class WakiMove(models.Model):
    _name = 'waki.move'
    _description = 'Waki Stock Move (quantity only, no valuation)'
    _order = 'date desc, id desc'

    name = fields.Char(default='New', readonly=True, copy=False)
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('done', 'Done')], default='draft', readonly=True, copy=False)
    waki_warehouse_id = fields.Many2one('waki.warehouse', required=True, index=True)
    company_id = fields.Many2one(related='waki_warehouse_id.company_id', store=True)
    product_id = fields.Many2one('product.product', required=True, index=True)
    uom_id = fields.Many2one(related='product_id.uom_id')
    quantity = fields.Float(digits='Product Unit of Measure', required=True)
    direction = fields.Selection([('in', 'In'), ('out', 'Out')], required=True)
    move_type = fields.Selection([
        ('purchase_receipt', 'Purchase Receipt'),
        ('purchase_return', 'Purchase Return'),
        ('mo_consume', 'Waki MO Consumption'),
        ('mo_produce', 'Waki MO Production'),
        ('adjustment', 'Manual Adjustment'),
    ], required=True, default='adjustment')
    origin = fields.Char()
    stock_move_id = fields.Many2one('stock.move', readonly=True, index=True, copy=False)
    waki_production_id = fields.Many2one(
        'waki.production', readonly=True, index=True, ondelete='cascade')
    production_id = fields.Many2one(related='waki_production_id.production_id', store=True)
    note = fields.Text()

    _sql_constraints = [
        ('quantity_positive', 'CHECK(quantity > 0)', 'Waki move quantity must be positive.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env['ir.sequence']
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = seq.next_by_code('waki.move') or 'New'
        return super().create(vals_list)

    def action_done(self):
        Stock = self.env['waki.stock'].sudo()
        for move in self.filtered(lambda m: m.state == 'draft'):
            delta = move.quantity if move.direction == 'in' else -move.quantity
            Stock._apply_delta(move.waki_warehouse_id, move.product_id, delta)
            move.write({'state': 'done', 'date': fields.Datetime.now()})
        return True

    @api.ondelete(at_uninstall=False)
    def _unlink_except_done(self):
        if any(m.state == 'done' for m in self):
            raise UserError(_('Posted waki moves cannot be deleted.'))
