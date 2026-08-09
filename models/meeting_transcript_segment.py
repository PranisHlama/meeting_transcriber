from odoo import fields, models


class MeetingTranscriptSegment(models.Model):
    _name = "meeting.transcript.segment"
    _description = "Meeting Transcript Segment"
    _order = "sequence, start_ms"

    transcription_id = fields.Many2one(
        comodel_name="meeting.transcription",
        string="Transcription",
        required=True,
        ondelete="cascade",
        index=True,
    )

    sequence = fields.Integer(
        required=True,
    )

    start_ms = fields.Integer(
        string="Start Time (ms)",
        required=True,
    )

    end_ms = fields.Integer(
        string="End Time (ms)",
        required=True,
    )

    text = fields.Text(
        required=True,
    )

    speaker_label = fields.Char()

    confidence = fields.Float()