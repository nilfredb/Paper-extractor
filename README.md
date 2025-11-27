# Scraping Tool — Technical README

## Overview
**Scraping Tool** is a modular and extensible web scraping framework built in Python.  
It is designed for high‑reliability data acquisition using Selenium, structured logging, multi‑phase scraping strategies, and a clean separation of responsibilities across modules.

This project is optimized for:
- Large‑scale scraping pipelines  
- Dynamic JavaScript-driven websites  
- Multi-step capture workflows (discovery → preparation → acquisition)  
- Extendability for future data extraction or automation tasks  

---

## Architecture

The project follows a **modular, layered architecture**, separated into the following core components:

### 1. `browser/`
Handles browser initialization and Selenium configuration.
- Headless/visible mode toggle  
- Custom user-agent support  
- Sniffer integration for detecting requests  
- Timeout, waits, retries  
- Error-safe driver context manager  

### 2. `discovery/`
Scans initial URLs to gather links or metadata needed to continue the pipeline.
- Strategy-based link extraction  
- Filtering mechanisms (regex, domain rules, patterns)  
- Logging of discovered items  

### 3. `preparation/`
Prepares the environment before final scraping.
- Cleans URLs  
- Performs authentication if required  
- Cookie/session validation  
- Resource warming or preloading  

### 4. `acquisition/`
The main data collection stage.
- Flexible extraction logic  
- DOM parsing  
- Snapshot saving (images, raw HTML, JSON, PDFs)  
- Error isolation per target  

### 5. `pipeline/`
Coordinates the entire workflow.
- Runs discovery → preparation → acquisition  
- Handles progress tracking  
- Saves structured logs  
- Supports future parallelization  

### 6. `logging/`
Centralized structured logging.
- Timestamps  
- Module origin  
- Log hierarchy (INFO, DEBUG, ERROR)  
- JSON log output (future-proof for ELK stack)  

### 7. `config/`
Project-wide configuration:
- Selenium settings  
- Timeouts  
- Proxy configuration  
- Output directories  
- Modular settings per scraping profile  

---

## Installation

### 1. Clone repository
```bash
git clone https://github.com/yourusername/scraping_tool.git
cd scraping_tool
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Install browser drivers  
If using Chrome/Chromium:
```bash
webdriver-manager
```

Or download manually and set path in config.

---

## Usage

### Basic Execution
```bash
python main.py --url https://example.com
```

### Advanced Configuration
Use a custom config file:
```bash
python main.py --config config/custom_config.json
```

### Running a Specific Pipeline Stage
```bash
python main.py --stage discovery
python main.py --stage acquisition
```

### Debug Mode
```bash
python main.py --debug
```

---

## Folder Structure

```
scraping_tool/
│
├── browser/
│   ├── driver.py
│   ├── sniffer.py
│   └── options.py
│
├── discovery/
│   └── discovery_engine.py
│
├── preparation/
│   └── preparation_engine.py
│
├── acquisition/
│   └── acquisition_engine.py
│
├── pipeline/
│   └── runner.py
│
├── logging/
│   └── logger.py
│
├── config/
│   ├── default.json
│   └── profiles/
│
├── output/
│   ├── logs/
│   ├── data/
│   └── snapshots/
│
└── main.py
```

---

## How It Works — Execution Flow

1. **Initialize environment**
   - Load config  
   - Create session UUID  
   - Initialize logger  
   - Launch Selenium driver  

2. **Discovery phase**
   - Extract URLs, IDs, metadata  
   - Apply filters  
   - Save discovery logs  

3. **Preparation phase**
   - Set cookies  
   - Preload elements  
   - Validate reachable pages  

4. **Acquisition phase**
   - Extract signals or content  
   - Save snapshots  
   - Return structured dataset  

5. **Finish**
   - Close driver  
   - Write final report  
   - Store logs and artifacts  

---

## Extending the Framework

### Add a new scraping strategy
Create file:
```
discovery/strategies/my_strategy.py
```

Implement:
```python
class MyStrategy:
    def run(self, driver):
        return discovered_items
```

Then enable in config:
```json
"discovery_strategies": ["my_strategy"]
```

---

## Logging

Logs are stored in:
```
output/logs/<date>/<session>.json
```

Each entry includes:
```json
{
  "timestamp": "...",
  "module": "acquisition",
  "level": "INFO",
  "message": "Item processed",
  "target": "https://example.com/page"
}
```

---

## Roadmap

- Parallel scraping (multiprocessing)
- Queue system (Redis / RabbitMQ)
- Plugin-based strategies  
- Metrics dashboard  
- API for controlling the scraper  
- Auto-scaling in containers  

---

## License
MIT License — free to use, modify, and extend.

---

## Author
Developed by **Nilfred Israel Báez del Rosario (Kilfred)**  
Python, AI & Offensive Security Developer
