# 🎙️ Anki Studio

![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Status](https://img.shields.io/badge/status-active-brightgreen)

**Anki Studio** is a powerful, free, and open-source desktop application designed to supercharge language learning. It automates the tedious process of creating Anki flashcards and generating high-quality audio narrations.

---

## 🌍 Free Software for the Community

This project is **100% Free and Open Source Software (FOSS)**. 
> **"The goal is for everyone to enjoy this tool for free. Copy it, study it, improve it, and share it!"**

We believe that language learning tools should be accessible to everyone, everywhere. No subscriptions, no hidden fees—just the community helping the community.

---

## ✨ What is Anki Studio?

Anki Studio provides an intuitive Graphical User Interface (GUI) to handle two main workflows:

1.  **Anki Flashcard Generator**: Turns your CSV files (vocabulary lists) into complete Anki Decks (`.apkg`) with high-quality AI-generated audio.
2.  **Graded Reader Narrator**: Converts long texts or stories into single MP3 files for listening practice.

---

## 🚀 Core Functionalities

### 1. Anki Flashcard Generator (Tab 1)
Transform simple text lists into professional study decks.
* **Flexible Mapping**: No fixed CSV structure required. Choose your columns on the fly.
* **AI Voices**: Powered by Microsoft Edge TTS with multiple natural voices (English, Italian, Spanish, German, and more).
* **Massive Efficiency**: Handles batch processing for large datasets with concurrent generation.
* **Smart Integration**: Automatically embeds audio into your cards.

### 2. Graded Reader Narrator (Tab 2)
Perfect for creating your own "Audiobooks" for language practice.
* **Text-to-Speech**: Paste any text and hear it narrated instantly.
* **Customizable**: Adjust the narrator's voice and speaking speed to match your level.
* **Export Ready**: Saves directly to MP3 for use on your phone or tablet.

---

## 🛠️ Technical Highlights

* **GUI**: Built with **Tkinter** for a native look and feel.
* **Speech Engine**: Uses **edge-tts** for high-fidelity, natural-sounding voices.
* **Async Processing**: Utilizes `asyncio` and `threading` to generate up to 20 audio files simultaneously without freezing the app.
* **Robustness**: Features exponential backoff retries, timeout protection, and detailed error logging.

---

## 📦 Installation & Quick Start

### Prerequisites
* Python 3.8+
* An active internet connection (for audio generation).

### Setup
1. **Clone/Download** this repository.
2. **Create a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/macOS
   # OR
   venv\Scripts\activate     # Windows

```

3. **Install dependencies**:
```bash
pip install edge-tts genanki

```


4. **Run the app**:
```bash
python anki_studio.py

```



---

## 📖 Example Scenario: Italian Learner

1. Open **Anki Studio** and load a CSV with Italian words.
2. Map "Word" to the audio source and "Sentence" to the target.
3. Select **Italian - Diego** and hit **GENERATE COMPLETE DECK**.
4. Import the resulting `.apkg` file into Anki.
5. **Start studying with native-like audio instantly!**

---

## 🤝 Contributing

Since this is a community project, contributions are more than welcome! Feel free to:

* Report bugs 🐛
* Suggest new features 💡
* Submit Pull Requests 🛠️

---

## 📜 License

This software is released under the **MIT License**. It is free to use, copy, modify, and distribute.

**Made with ❤️ for language learners around the world.**
