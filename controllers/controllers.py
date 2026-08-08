# -*- coding: utf-8 -*-
# from odoo import http


# class MeetingTranscriber(http.Controller):
#     @http.route('/meeting_transcriber/meeting_transcriber', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/meeting_transcriber/meeting_transcriber/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('meeting_transcriber.listing', {
#             'root': '/meeting_transcriber/meeting_transcriber',
#             'objects': http.request.env['meeting_transcriber.meeting_transcriber'].search([]),
#         })

#     @http.route('/meeting_transcriber/meeting_transcriber/objects/<model("meeting_transcriber.meeting_transcriber"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('meeting_transcriber.object', {
#             'object': obj
#         })

