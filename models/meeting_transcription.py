import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


ALLOWED_RECORDING_EXTENSIONS = {".m4a", ".mp3", ".mp4", ".wav", ".webm"}
FFPROBE_TIMEOUT = 30
FFMPEG_TIMEOUT = 300
EXTRACTED_AUDIO_EXTENSION = ".wav"


class MeetingTranscription(models.Model):
    _name = "meeting.transcription"
    _description = "Meeting Transcription"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True)

    calendar_event_id = fields.Many2one(
        "calendar.event",
        required=True,
        ondelete="cascade",
        index=True,
    )

    recording_file = fields.Binary(
        string="Meeting Recording",
        attachment=True,
        required=True,
    )

    recording_filename = fields.Char(
        string="Recording Filename",
    )

    extracted_audio_file = fields.Binary(
        string="Extracted Audio",
        attachment=True,
        readonly=True,
    )

    extracted_audio_filename = fields.Char(
        string="Extracted Audio Filename",
        readonly=True,
    )

    error_message = fields.Text(
        string="Error Message",
        readonly=True,
    )

    media_metadata = fields.Json(
        string="Media Metadata",
        readonly=True,
    )

    state = fields.Selection([
        ("draft", "Draft"),
        ("validating", "Validating"),
        ("queued", "Queued"),
        ("extracting", "Extracting Audio"),
        ("transcribing", "Transcribing"),
        ("summarizing", "Generating Summary"),
        ("review", "Needs Review"),
        ("published", "Published"),
        ("failed", "Failed"),
    ], default="draft", tracking=True)

    def action_process_recording(self):
        for record in self:
            if not record.recording_file:
                raise UserError("No recording file found.")
            # Run extension validation before moving the record forward.
            record._validate_extension()
            record.write({
                "state": "validating",
                "error_message": False
            })
        return True
    
    def _validate_extension(self):
        self.ensure_one()

        suffix = Path(
            self.recording_filename or ""
        ).suffix.lower()

        if suffix not in ALLOWED_RECORDING_EXTENSIONS:
            raise ValidationError(
                f"Unsupported file format: {suffix or 'unknown'}"
            )

        return suffix