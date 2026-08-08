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
MAX_DURATION_SECONDS = 3600
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB
MIN_DURATION_SECONDS = 2

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
            record._inspect_recording_file()
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

    # Inspect the file with ffprobe
    def inspect_media(self, file_path, metadata=None):
        duration = float(metadata.get("format", {}).get("duration", 0.0))
        file_size = int(metadata.get("format", {}).get("size", 0))

        if duration < MIN_DURATION_SECONDS:
            raise ValidationError(
                "The recording is too short."
            )

        if duration > MAX_DURATION_SECONDS:
            raise ValidationError(
                "The recording exceeds the four-hour limit."
            )

        if file_size > MAX_FILE_SIZE:
            raise ValidationError(
                "The recording exceeds the maximum file size."
            )
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,format_name,size:"
                "stream=index,codec_type,codec_name,"
                "sample_rate,channels"
            ),
            "-of",
            "json",
            str(file_path),
        ]

        result = subprocess.run(
            command,
            capture_output = True,
            check=False,
            timeout=60,
        )

        if result.returncode != 0:
            error = result.stderr.decode(
                "utf-8",
                errors="replace",
            )
            raise ValidationError(
                f"The uploaded file is not a valid media: {error}"
            )
        return json.loads(result.stdout)

    def find_audio_stream(self, metadata):
        audio_streams = [
            stream for stream in metadata.get("streams", [])
            if stream.get("codec_type") == "audio"
        ]

        if not audio_streams:
            raise ValidationError(
                "No audio stream found in the uploaded media."
            )

        return audio_streams[0]

    def extract_audio(self, input_path, output_path):
        command = [
            "ffmpeg",
            "-i",
            str(input_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(output_path),
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=FFMPEG_TIMEOUT,
        )

        if result.returncode != 0:
            error = result.stderr.decode(
                "utf-8",
                errors="replace",
            )
            raise ValidationError(
                f"Failed to extract audio: {error}"
            )
    
