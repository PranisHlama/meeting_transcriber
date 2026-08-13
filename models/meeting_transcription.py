import base64
import html
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests
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

    recording_audio_player = fields.Html(
        string="Recording Player",
        compute="_compute_audio_players",
        sanitize=False,
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

    extracted_audio_player = fields.Html(
        string="Extracted Audio Player",
        compute="_compute_audio_players",
        sanitize=False,
    )

    error_message = fields.Text(
        string="Error Message",
        readonly=True,
    )

    media_metadata = fields.Json(
        string="Media Metadata",
        readonly=True,
    )

    duration_seconds = fields.Float(readonly=True)
    file_size = fields.Integer(readonly=True)
    container_format = fields.Char(readonly=True)
    audio_codec = fields.Char(readonly=True)
    sample_rate = fields.Integer(readonly=True)
    channel_count = fields.Integer(readonly=True)

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

    segment_ids = fields.One2many(
        comodel_name="meeting.transcript.segment",
        inverse_name="transcription_id",
        string="Transcript Segments",
    )

    @api.depends("recording_file", "extracted_audio_file")
    def _compute_audio_players(self):
        """Build native HTML5 audio players for the stored binary files."""
        for record in self:
            record.recording_audio_player = record._audio_player_html(
                "recording_file", record.recording_file
            )
            record.extracted_audio_player = record._audio_player_html(
                "extracted_audio_file", record.extracted_audio_file
            )

    def _audio_player_html(self, field_name, file_value):
        if not file_value or not self.id:
            return False
        src = "/web/content/meeting.transcription/%s/%s?download=0" % (
            self.id,
            field_name,
        )
        return (
            '<audio controls preload="metadata" style="width: 100%%;">'
            '<source src="%s">'
            "Your browser does not support the audio player."
            "</audio>"
        ) % html.escape(src, quote=True)


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

    def action_transcript_audio(self):
        for record in self:
            if not record.extracted_audio_file:
                raise UserError("No extracted audio file found.")
            try:
                record.write({"state": "transcribing", "error_message": False})
                transcription_result = record._transcribe_extracted_audio()
                record._replace_transcript_segments(transcription_result)
                record.write({
                    "state": "review",
                    "error_message": False,
                })
            except Exception as e:
                record.write({
                    "state": "failed",
                    "error_message": str(e),
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

    def inspect_media(self, file_path):
        ffprobe_path = shutil.which("ffprobe")
        if not ffprobe_path:
            raise ValidationError(
                "ffprobe is not installed or not available on the server PATH."
            )

        command = [
            ffprobe_path,
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

        metadata = json.loads(result.stdout)
        format_metadata = metadata.get("format", {})
        duration = float(format_metadata.get("duration", 0.0))
        file_size = int(format_metadata.get("size", 0))

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

        return metadata

    def find_audio_stream(self, metadata):
        """
            Find the first audio stream in media metadata. 
            Returns: dict: Audio stream metadata. 
            Raises: ValidationError: If no audio stream exists. 
        """
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
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            raise ValidationError(
                "ffmpeg is not installed or not available on the server PATH."
            )

        command = [
            ffmpeg_path,
            "-i",
            str(input_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-y",
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
        
        

    def _extract_media_field_values(self, metadata):
        format_metadata = metadata.get("format", {})
        audio_stream = self.find_audio_stream(metadata)

        sample_rate = audio_stream.get("sample_rate")
        channel_count = audio_stream.get("channels")

        return {
            "duration_seconds": float(format_metadata.get("duration", 0.0)),
            "file_size": int(format_metadata.get("size", 0)),
            "container_format": format_metadata.get("format_name"),
            "audio_codec": audio_stream.get("codec_name"),
            "sample_rate": int(sample_rate) if sample_rate else False,
            "channel_count": int(channel_count) if channel_count else False,
        }

    def _inspect_recording_file(self):
        self.ensure_one()

        suffix = self._validate_extension()
        recording_bytes = base64.b64decode(self.recording_file)
        extracted_audio_path = None

        with tempfile.NamedTemporaryFile(
            suffix=suffix,
            delete=False,
        ) as temp_file:
            temp_file.write(recording_bytes)
            temp_path = Path(temp_file.name)

        try:
            metadata = self.inspect_media(temp_path)
            field_values = self._extract_media_field_values(metadata)

            with tempfile.NamedTemporaryFile(
                suffix=EXTRACTED_AUDIO_EXTENSION,
                delete=False,
            ) as extracted_audio_file:
                extracted_audio_path = Path(extracted_audio_file.name)

            self.extract_audio(temp_path, extracted_audio_path)
            extracted_audio_filename = (
                f"{Path(self.recording_filename or self.name).stem}"
                f"{EXTRACTED_AUDIO_EXTENSION}"
            )

            self.write({
                "media_metadata": metadata,
                "extracted_audio_file": base64.b64encode(
                    extracted_audio_path.read_bytes()
                ),
                "extracted_audio_filename": extracted_audio_filename,
                **field_values,
            })
        finally:
            if temp_path.exists():
                os.unlink(temp_path)
            if extracted_audio_path and extracted_audio_path.exists():
                os.unlink(extracted_audio_path)

    def split_audio(self, input_path, output_dir):
        command = [
            "ffmpeg",
            "-i",
            str(input_path),
            "-f",
            "segment",
            "-segment_time",
            "600",
            "-c",
            "copy",
            str(output_dir / "segment_%03d.wav")
        ]

    def _transcribe_extracted_audio(self):
        self.ensure_one()

        audio_bytes = base64.b64decode(self.extracted_audio_file)
        with tempfile.NamedTemporaryFile(
            suffix=EXTRACTED_AUDIO_EXTENSION,
            delete=False,
        ) as audio_file:
            audio_file.write(audio_bytes)
            audio_path = Path(audio_file.name)

        try:
            return self.transcript_audio(audio_path)
        finally:
            if audio_path.exists():
                os.unlink(audio_path)

    def _replace_transcript_segments(self, transcription_result):
        self.ensure_one()

        segments = transcription_result.get("segments") or []
        if not segments and transcription_result.get("text"):
            segments = [{
                "start": 0,
                "end": transcription_result.get("duration") or 0,
                "text": transcription_result["text"],
            }]

        self.segment_ids.unlink()
        self.env["meeting.transcript.segment"].create([
            {
                "transcription_id": self.id,
                "sequence": index + 1,
                "start_ms": self._seconds_to_ms(segment.get("start")),
                "end_ms": self._seconds_to_ms(segment.get("end")),
                "text": segment.get("text", "").strip(),
                "speaker_label": segment.get("speaker"),
                "confidence": segment.get("confidence") or 0.0,
            }
            for index, segment in enumerate(segments)
            if segment.get("text")
        ])

    @staticmethod
    def _seconds_to_ms(value):
        return int(float(value or 0) * 1000)

    def transcript_audio(self, audio_file_path):
        with open(audio_file_path, "rb") as audio_file:
            response = requests.post(
                "http://127.0.0.1:8010/transcribe",
                files={
                    "file": (
                        audio_file_path.name,
                        audio_file,
                        "audio/wav",
                    )
                },
                timeout=1800,
            )

        response.raise_for_status()
        return response.json()
