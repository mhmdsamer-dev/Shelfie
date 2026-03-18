# Shelfie - Local Library Manager
---
[![PyPI version](https://img.shields.io/pypi/v/shelfie-py.svg)](https://pypi.org/project/shelfie-py/)
**Shelfie** is a fast, self-hosted web application for managing your personal library of PDFs and EPUBs.
Designed with simplicity in mind, Shelfie automatically watches your book folder for new files, generates covers, and provides a clean, responsive web interface to browse and search your collection.

## Screenshots
---
![SHELFIE](docs/shelfie_webUI_1.png)
![SHELFIE](docs/shelfie_webUI_2.png)

## Installation
---
### Pip
The stable releases of _shelfie_ are distributed on [PyPI](https://pypi.org/) and can be easily installed or upgraded using [pip](https://pip.pypa.io/en/stable/):
```
pip install shelfie-py
shelfie
```
### Docker
```
git clone https://github.com/mhmdsamer-dev/shelfie.git
cd shelfie/
docker build -t shelfie .
BOOKS_DIR=REPLACE/YOUR/BOOK/PATH docker compose up -d
```

Pulling image from [Docker Hub](https://hub.docker.com/r/mhmdsamerdev/shelfie):
```
docker run -d -p 8000:8000 -e LIBRARY_PATH=/books -v shelfie_data:/data -v "D:\Your\Books\Folder":/books:ro mhmdsamerdev/shelfie
```

## CLI options
---
```
shelfie [--host HOST] [--port PORT] [--reload] [--log-level LEVEL]
  --host       Bind address (default: 127.0.0.1)
  --port       Port number  (default: 8000)
  --reload     Auto-reload on code changes (development)
  --log-level  debug | info | warning | error  (default: info)
```

All user data is stored under `SHELFIE_DATA_DIR` (default `~/.shelfie`)

## 🤝 Contributing
---
Please open an issue first to discuss what you would like to change.