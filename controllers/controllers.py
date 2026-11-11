# -*- coding: utf-8 -*-
# from odoo import http


# class KioVatSubmissionExtension(http.Controller):
#     @http.route('/kio_vat_submission_extension/kio_vat_submission_extension', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/kio_vat_submission_extension/kio_vat_submission_extension/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('kio_vat_submission_extension.listing', {
#             'root': '/kio_vat_submission_extension/kio_vat_submission_extension',
#             'objects': http.request.env['kio_vat_submission_extension.kio_vat_submission_extension'].search([]),
#         })

#     @http.route('/kio_vat_submission_extension/kio_vat_submission_extension/objects/<model("kio_vat_submission_extension.kio_vat_submission_extension"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('kio_vat_submission_extension.object', {
#             'object': obj
#         })

