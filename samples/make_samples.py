import json
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

DEMO = {
    "package.json": json.dumps({"name": "notes-desktop", "version": "1.0.0", "main": "main.js"}, indent=2),
    "main.js": """const { app, BrowserWindow } = require('electron')
const config = require('./config.js')

function createWindow() {
  const win = new BrowserWindow({ width: 1000, height: 700 })
  win.loadFile('index.html')
  win.webContents.openDevTools()
}

app.whenReady().then(createWindow)
""",
    "config.js": """module.exports = {
  apiBase: 'http://localhost:3000/api',
  apiKey: 'sk_test_FAKE1234567890abcdef',
  debug: true,
  payments: { mode: 'fake', chargeCards: false }
}
""",
    "renderer.js": """// TODO: add offline sync before release
async function loadNotes() {
  const res = await fetch('https://notes-api.example.com/v1/notes')
  return res.json()
}
""",
    "renderer.js.map": '{"version":3,"sources":["renderer.ts"],"mappings":"AAAA"}',
    "index.html": "<!doctype html><html><body><h1>Notes</h1><script src=\"renderer.js\"></script></body></html>",
    ".env": "DB_PASSWORD=not_a_real_password_123\n",
}

CLEAN = {
    "package.json": json.dumps({"name": "notes-desktop", "version": "1.0.1", "main": "main.js"}, indent=2),
    "main.js": """const { app, BrowserWindow } = require('electron')

function createWindow() {
  const win = new BrowserWindow({ width: 1000, height: 700 })
  win.loadFile('index.html')
}

app.whenReady().then(createWindow)
""",
    "config.js": """module.exports = {
  apiBase: 'https://notes-api.example.com/v1',
  debug: false
}
""",
    "renderer.js": """async function loadNotes() {
  const res = await fetch('https://notes-api.example.com/v1/notes')
  return res.json()
}
""",
    "index.html": "<!doctype html><html><body><h1>Notes</h1><script src=\"renderer.js\"></script></body></html>",
}

EXPECTED = {
    "demo_app.zip": ["secret_key", "debug_on", "test_url", "dev_file", "todo_note"],
    "clean_app.zip": [],
}


def write(name: str, files: dict) -> Path:
    out = HERE / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, text in files.items():
            z.writestr(path, text)
    return out


def main():
    write("demo_app.zip", DEMO)
    write("clean_app.zip", CLEAN)
    (HERE / "expected.json").write_text(json.dumps(EXPECTED, indent=2), encoding="utf-8")
    print("Created demo_app.zip, clean_app.zip, expected.json")


if __name__ == "__main__":
    main()
