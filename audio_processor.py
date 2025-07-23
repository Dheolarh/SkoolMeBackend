import os
import threading
import time
from datetime import timedelta
from collections import defaultdict
import logging

from pydub import AudioSegment
from google.cloud import speech_v1p1beta1 as speech
from google.cloud import storage

# Set up Google Cloud credentials
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "skoolme-ocr-b933da63cd81.json"

logger = logging.getLogger(__name__)

class AudioProcessor:
    def __init__(self):
        self.gcs_bucket_name = "skoolme-audio-transcripts"
        self.speech_client = speech.SpeechClient()
        self.storage_client = storage.Client()
        self.progress_callback = None
    
    def set_progress_callback(self, callback):
        """Set callback function for progress updates"""
        self.progress_callback = callback
    
    def process_audio(self, file_path):
        """Process audio file and return transcript"""
        try:
            self._update_progress("Starting audio processing...")
            
            # Convert to WAV format
            self._update_progress("Converting audio to WAV format...")
            wav_path = self._convert_to_wav(file_path)
            
            # Upload to Google Cloud Storage
            self._update_progress("Uploading to Google Cloud Storage...")
            gcs_uri = self._upload_to_gcs(wav_path)
            
            # Transcribe audio
            self._update_progress("Transcribing audio...")
            transcript = self._transcribe_audio(gcs_uri)
            
            # Clean up temporary files
            self._cleanup_temp_files([wav_path])
            
            self._update_progress("Audio processing completed!")
            return transcript
        
        except Exception as e:
            logger.error(f"Audio processing failed: {str(e)}")
            raise
    
    def _convert_to_wav(self, audio_path):
        """Convert audio file to WAV format"""
        try:
            # Generate temporary WAV file path
            base_name = os.path.splitext(os.path.basename(audio_path))[0]
            wav_path = os.path.join(os.path.dirname(audio_path), f"{base_name}_converted.wav")
            
            # Load and convert audio
            audio = AudioSegment.from_file(audio_path)
            audio = audio.set_frame_rate(16000).set_channels(1)
            audio.export(wav_path, format="wav")
            
            return wav_path
        
        except Exception as e:
            logger.error(f"Audio conversion failed: {str(e)}")
            raise
    
    def _upload_to_gcs(self, file_path):
        """Upload file to Google Cloud Storage"""
        try:
            bucket = self.storage_client.bucket(self.gcs_bucket_name)
            blob_name = f"audio_{int(time.time())}_{os.path.basename(file_path)}"
            blob = bucket.blob(blob_name)
            
            blob.upload_from_filename(file_path)
            
            return f"gs://{self.gcs_bucket_name}/{blob_name}"
        
        except Exception as e:
            logger.error(f"GCS upload failed: {str(e)}")
            raise
    
    def _transcribe_audio(self, gcs_uri):
        """Transcribe audio using Google Speech-to-Text (matches original working script)"""
        try:
            self._update_progress("Starting transcription...")
            
            audio = speech.RecognitionAudio(uri=gcs_uri)
            
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
                language_code="en-US",
                enable_word_time_offsets=True,
                enable_automatic_punctuation=True,
                model="latest_long",
            )
            
            # Start long-running operation
            operation = self.speech_client.long_running_recognize(config=config, audio=audio)
            self._update_progress("Transcribing (0%)")
            
            # Poll operation and simulate progress (matches original script)
            progress = 0
            while not operation.done():
                time.sleep(5)
                progress = min(progress + 5, 95)  # Simulate progress until done
                self._update_progress(f"Transcribing ({progress}%)")
            
            # Get results
            result = operation.result(timeout=1000)
            self._update_progress("Transcribing (100%)\nCompleted.")
            
            return self._format_transcript(result)
        
        except Exception as e:
            logger.error(f"Transcription failed: {str(e)}")
            raise
    
    def _format_transcript(self, result):
        """Format transcript with timestamps (matches original working script)"""
        try:
            full_transcript = ""
            chunk_transcripts = defaultdict(list)
            chunk_duration = 120  # seconds (matches original)
            
            for result_chunk in result.results:
                if not result_chunk.alternatives:
                    continue
                
                alternative = result_chunk.alternatives[0]
                
                # Handle word-level timestamps (matches original implementation)
                if alternative.words:
                    for word_info in alternative.words:
                        start_sec = word_info.start_time.total_seconds()
                        chunk_index = int(start_sec // chunk_duration)
                        chunk_transcripts[chunk_index].append((word_info.start_time, word_info.word))
                else:
                    # Fallback for results without word-level timestamps
                    chunk_transcripts[0].append((None, alternative.transcript))
            
            # Format output with timestamps (matches original)
            for chunk_index in sorted(chunk_transcripts.keys()):
                start_time = str(timedelta(seconds=chunk_index * chunk_duration))[:-3]
                end_time = str(timedelta(seconds=(chunk_index + 1) * chunk_duration))[:-3]
                
                full_transcript += f"\n\nTimestamp: {start_time} - {end_time}\n"
                
                chunk_text = " ".join(word for _, word in chunk_transcripts[chunk_index])
                full_transcript += chunk_text
            
            return full_transcript.strip()
        
        except Exception as e:
            logger.error(f"Transcript formatting failed: {str(e)}")
            # Return basic transcript as fallback
            basic_transcript = ""
            for result_chunk in result.results:
                if result_chunk.alternatives:
                    basic_transcript += result_chunk.alternatives[0].transcript + " "
            return basic_transcript.strip()
    
    def _cleanup_temp_files(self, file_paths):
        """Clean up temporary files"""
        for file_path in file_paths:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                logger.warning(f"Failed to cleanup temporary file {file_path}: {str(e)}")
    
    def _update_progress(self, message):
        """Update progress via callback"""
        if self.progress_callback:
            self.progress_callback(message)
        logger.info(message)
