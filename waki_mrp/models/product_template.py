from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    waki_bom_count = fields.Integer(compute='_compute_waki_bom_count')

    def _compute_waki_bom_count(self):
        data = dict(self.env['waki.bom']._read_group(
            [('product_tmpl_id', 'in', self.ids)], ['product_tmpl_id'], ['__count']))
        for tmpl in self:
            tmpl.waki_bom_count = data.get(tmpl, 0)

    def action_view_waki_boms(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('waki_mrp.action_waki_bom')
        action['domain'] = [('product_tmpl_id', '=', self.id)]
        action['context'] = {'default_product_tmpl_id': self.id,
                             'default_product_uom_id': self.uom_id.id}
        return action
