# Speech-to-text evaluation

## FLEURS test set (first 100 clips per language)

| Language | Model | WER | CER | p50 latency |
|---|---|---|---|---|
| hi_in | whisper-large-v3-turbo | 32.1% | 11.6% | 739 ms |
| hi_in | whisper-large-v3 | 26.9% | 9.6% | 978 ms |
| en_us | whisper-large-v3-turbo | 4.9% | 2.4% | 509 ms |
| en_us | whisper-large-v3 | 4.6% | 2.2% | 662 ms |

## Clinic details in synthetic utterances

Phone numbers, doctor names and days in clinic sentences, synthesised with Indian neural voices.

| Model | Style | Utterances | Details correct | Sentences fully correct | Urdu-script retries | p50 latency |
|---|---|---|---|---|---|---|
| whisper-large-v3-turbo | en | 5 | 100% | 100% | 0 | 288 ms |
| whisper-large-v3-turbo | hi | 5 | 92% | 80% | 0 | 246 ms |
| whisper-large-v3-turbo | hinglish | 5 | 82% | 60% | 0 | 222 ms |
| whisper-large-v3 | en | 5 | 100% | 100% | 0 | 392 ms |
| whisper-large-v3 | hi | 5 | 75% | 40% | 0 | 333 ms |
| whisper-large-v3 | hinglish | 5 | 91% | 80% | 0 | 340 ms |

Text-to-speech p50 latency: 821 ms.
