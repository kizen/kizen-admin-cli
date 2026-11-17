# t15_contact_voicemail: Transcribe an audio file with a model the environment supports

Tier 3 · contacts

Covers triggers: manual  
Covers steps: audio_transcription

## Before the session

- The business needs a model that can transcribe audio (`kizen automations llm-models`).

## Prompt

Create an automation on Contacts named "{p} Voicemail Transcript" with api_name `{p}_voicemail_transcript`. It is started manually.

It has one step: an audio transcription step that transcribes the file in the contact's `eval_voicemail` field with the prompt "Transcribe this voicemail." and writes the transcript to the contact's `eval_voicemail_transcript` field. Use any model that supports audio transcription in this environment.

## Rubric

- **reads_voicemail**: The transcription step reads the field eval_voicemail with the prompt "Transcribe this voicemail.".
- **writes_transcript**: It writes to the field eval_voicemail_transcript.
