from odoo import _, api, fields, models
from odoo.exceptions import UserError


class WakiBom(models.Model):
    _name = 'waki.bom'
    _description = 'Shadow (Waki) Bill of Materials'
    _inherit = ['mail.thread']
    _rec_name = 'product_tmpl_id'
    _order = 'product_tmpl_id, id'

    code = fields.Char(string='Reference')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    product_tmpl_id = fields.Many2one(
        'product.template', required=True, index=True, tracking=True)
    product_id = fields.Many2one(
        'product.product', string='Variant', index=True, tracking=True,
        domain="[('product_tmpl_id', '=', product_tmpl_id)]")
    product_qty = fields.Float(
        string='Quantity', default=1.0, required=True, digits='Product Unit of Measure',
        tracking=True)
    product_uom_category_id = fields.Many2one(related='product_tmpl_id.uom_id.category_id')
    product_uom_id = fields.Many2one(
        'uom.uom', string='Unit', required=True,
        domain="[('category_id', '=', product_uom_category_id)]")
    source_bom_id = fields.Many2one('mrp.bom', string='Company BoM', readonly=True)
    line_ids = fields.One2many('waki.bom.line', 'bom_id', copy=True)

    _sql_constraints = [
        ('qty_positive', 'CHECK(product_qty > 0)', 'BoM quantity must be positive.'),
    ]

    @api.onchange('product_tmpl_id')
    def _onchange_product_tmpl_id(self):
        if self.product_tmpl_id:
            self.product_uom_id = self.product_tmpl_id.uom_id

    def _compute_display_name(self):
        for bom in self:
            name = (bom.product_id or bom.product_tmpl_id).display_name or ''
            bom.display_name = f'{bom.code}: {name}' if bom.code else name

    @api.model
    def _find_for_product(self, product, company):
        domain = [('company_id', 'in', [company.id, False])]
        bom = self.search(domain + [('product_id', '=', product.id)], limit=1)
        if not bom:
            bom = self.search(domain + [('product_id', '=', False),
                                        ('product_tmpl_id', '=', product.product_tmpl_id.id)],
                              limit=1)
        return bom

    @api.model
    def _prepare_lines_from_bom(self, bom, product):
        """Explode the real BoM (phantom kits included) at its base quantity."""
        __, lines_done = bom.explode(product, bom.product_qty)
        return [(0, 0, {
            'product_id': line.product_id.id,
            'product_qty': data['qty'],
            'product_uom_id': line.product_uom_id.id,
            'sequence': line.sequence,
        }) for line, data in lines_done]

    @api.model
    def _get_or_create(self, product, real_bom, company):
        bom = self._find_for_product(product, company)
        if bom or not real_bom:
            return bom
        return self.create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_id': product.id if real_bom.product_id else False,
            'product_qty': real_bom.product_qty,
            'product_uom_id': real_bom.product_uom_id.id,
            'code': real_bom.code,
            'company_id': real_bom.company_id.id,
            'source_bom_id': real_bom.id,
            'line_ids': self._prepare_lines_from_bom(real_bom, product),
        })

    def action_refresh_from_source_bom(self):
        """Manual only: re-copy lines from the real BoM. Never done automatically."""
        for bom in self:
            if not bom.source_bom_id:
                raise UserError(_('This waki BoM has no source BoM.'))
            product = bom.product_id or bom.product_tmpl_id.product_variant_id
            bom.line_ids.unlink()
            bom.write({
                'product_qty': bom.source_bom_id.product_qty,
                'product_uom_id': bom.source_bom_id.product_uom_id.id,
                'line_ids': self._prepare_lines_from_bom(bom.source_bom_id, product),
            })
            bom.message_post(body=_('Components refreshed from %s.',
                                    bom.source_bom_id.display_name))
        return True


class WakiBomLine(models.Model):
    _name = 'waki.bom.line'
    _description = 'Waki BoM Line'
    _order = 'sequence, id'

    sequence = fields.Integer(default=1)
    bom_id = fields.Many2one('waki.bom', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', required=True)
    product_qty = fields.Float(
        string='Quantity', default=1.0, required=True, digits='Product Unit of Measure')
    product_uom_category_id = fields.Many2one(related='product_id.uom_id.category_id')
    product_uom_id = fields.Many2one(
        'uom.uom', string='Unit', required=True,
        domain="[('category_id', '=', product_uom_category_id)]")

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.product_uom_id = self.product_id.uom_id
