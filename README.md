# Sony Photos Sync

Automatically transfer photos from your Sony Alpha camera to Google Photos — completely hands-free.

**How it works:** Your Sony camera sends photos via WiFi FTP to your Mac, which uploads them to Google Photos automatically. Take photos anywhere — they sync when you get home.

```
Sony Alpha Camera --[WiFi/FTP]--> Mac (FTP Server) --> Google Photos
```

## Features

- **Automatic sync** — photos transfer as soon as your camera connects to WiFi
- **macOS menu bar app** — lightweight, runs in the background
- **Duplicate detection** — perceptual hashing prevents re-uploading the same photo
- **Works offline** — photos queue on the camera and sync when back on WiFi
- **Album support** — uploads to a specific Google Photos album

## Supported Cameras

Any Sony Alpha camera with built-in FTP transfer:
- A7R V, A7R IV
- A7 IV, A7 III
- A7S III
- A9 III, A9 II
- A1
- And others with FTP Transfer in the menu

## Requirements

- macOS 12+
- Python 3.9+
- [rclone](https://rclone.org/) (`brew install rclone`)

## Installation

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/sony-photos-sync.git
cd sony-photos-sync

# Install Python dependencies
pip3 install -r requirements.txt

# Install rclone
brew install rclone

# Run the app
python3 app.py
```

## First-Time Setup

### 1. Configure Google Photos

Click **"Configure Google Photos..."** in the menu bar app. This opens a browser window to authorize access to your Google Photos account. The app can only **upload** photos — it cannot delete or modify existing photos (Google's API restriction).

### 2. Configure Your Camera

On your Sony camera:

1. **Menu → Network → Wi-Fi → Access Point Set** — connect to your home WiFi
2. **Menu → Network → FTP Transfer → Server Setting** — enter:
   - **Hostname**: Your Mac's IP (shown in the menu bar app)
   - **Port**: `2121`
   - **User**: `sony`
   - **Password**: `photos`
   - **Secure Protocol**: Off
3. **Menu → Network → FTP Transfer → Auto Transfer** → **On**

That's it! Every photo you take will automatically sync to Google Photos when your camera is on your home WiFi.

## How It Works

1. **FTP Server** — the app runs a lightweight FTP server on port 2121
2. **Folder Watcher** — monitors the incoming folder for new photos
3. **Dedup Check** — computes a perceptual hash to skip duplicates
4. **Upload** — sends the photo to Google Photos via rclone
5. **Archive** — moves the photo to the uploaded folder

## Configuration

Config file: `~/Library/Application Support/SonyPhotosSync/config.yaml`

```yaml
ftp:
  host: "0.0.0.0"
  port: 2121
  username: "sony"
  password: "photos"

rclone:
  remote_name: "gphotos"
  album: "Sony Alpha"    # Google Photos album name

upload:
  extensions: [".jpg", ".jpeg"]
  settle_time: 3
  max_workers: 2
```

## Privacy & Security

- The app **only uploads** photos — it cannot delete or modify your Google Photos library
- FTP runs on your local network only (not exposed to the internet)
- Your Google OAuth credentials are stored locally in `~/Library/Application Support/SonyPhotosSync/`
- No data is sent anywhere except Google Photos

## License

MIT
