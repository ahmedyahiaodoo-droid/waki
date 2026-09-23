from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class WakiStock(models.Model):
    _name = 'waki.stock'
    _description = 'Waki Stock'
    _rec_name = 'product_id'
    _order = 'waki_warehouse_id, product_id'

    waki_warehouse_id = fields.Many2one(
        'waki.warehouse', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='waki_warehouse_id.company_id', store=True)
    product_id = fields.Many2one('product.product', required=True, index=True)
    product_tmpl_id = fields.Many2one(related='product_id.product_tmpl_id', store=True)
    uom_id = fields.Many2one(related='product_id.uom_id')
    quantity = fields.Float(digits='Product Unit of Measure', readonly=True)

    _sql_constraints = [
        ('warehouse_product_uniq', 'unique(waki_warehouse_id, product_id)',
         'Only one waki stock line per product and waki warehouse.'),
    ]

    @api.model
    def _apply_delta(self, waki_warehouse, product, delta):
        stock = self.search([('waki_warehouse_id', '=', waki_warehouse.id),
                             ('product_id', '=', product.id)], limit=1)
        if not stock:
            stock = self.create({'waki_warehouse_id': waki_warehouse.id,
                                 'product_id': product.id})
        self.env.cr.execute(
            'SELECT id FROM waki_stock WHERE id = %s FOR UPDATE', (stock.id,))
        stock.invalidate_recordset(['quantity'])
        new_qty = stock.quantity + delta
        if (not waki_warehouse.allow_negative and float_compare(
                new_qty, 0, precision_rounding=product.uom_id.rounding) < 0):
            raise UserError(_(
                'Not enough waki stock for %(product)s in %(wh)s '
                '(available %(avail)s, required %(req)s).',
                product=product.display_name, wh=waki_warehouse.name,
                avail=stock.quantity, req=-delta))
        stock.quantity = new_qty
        return stock
