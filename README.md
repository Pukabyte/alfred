<div align="center">
  <a href="https://github.com/Pukabyte/alfred">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="web/assets/logo-dark.png" width="400">
      <img alt="alfred" src="web/assets/logo-dark.png" width="400">
    </picture>
  </a>
</div>

<div align="center">
  <a href="https://github.com/Pukabyte/alfred/stargazers"><img alt="GitHub Repo stars" src="https://img.shields.io/github/stars/Pukabyte/alfred?label=Alfred"></a>
  <a href="https://github.com/Pukabyte/alfred/issues"><img alt="Issues" src="https://img.shields.io/github/issues/Pukabyte/alfred" /></a>
  <a href="https://github.com/Pukabyte/alfred/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/Pukabyte/alfred"></a>
  <a href="https://github.com/Pukabyte/alfred/graphs/contributors"><img alt="Contributors" src="https://img.shields.io/github/contributors/Pukabyte/alfred" /></a>
  <a href="https://discord.gg/vMSnNcd7m5"><img alt="Discord" src="https://img.shields.io/badge/Join%20discord-8A2BE2" /></a>
</div>

<div align="center">
  <p>Symlink management made simple.</p>
</div>

# Alfred - Symlink Manager

Alfred is a Docker container that monitors symlinks in a specified directory and manages their target files. When a symlink is deleted, Alfred will automatically delete the target file if it's no longer referenced by any other symlinks.

## Features

- Real-time monitoring of symlink changes
- Automatic deletion of unreferenced target files
- Reference counting to prevent premature deletion
- Persistent database to track symlinks and their targets
- Docker container for easy deployment
- Saltbox integration ready
- Web-based management interface
- Real-time dashboard with statistics
- Historical metrics tracking
- Directory status monitoring
- Backup and restore functionality
- Configurable scan intervals
- Multiple directory support
- Flexible delete behavior (files or folders)
- Dry-run mode for testing
- Search and filter capabilities
- Pagination and sorting
- Detailed symlink information
- Automatic background scanning
- Comprehensive logging
- Multi-platform support (linux/amd64, linux/arm64, linux/arm/v7)

## How It Works

1. **Delete Non-linked files**
   - Scan all current symlinks in symlinks directory
   - Cross reference with realdebrid mount directory
   - Deletes all files that are not currently symlinked 

2. **Symlink Monitoring**:
   - Watches specified directories for symlink changes
   - Tracks all symlinks and their target files in a SQLite database
   - Maintains a reference count for each target file
   - Real-time updates through web interface

3. **Event Handling**:
   - **Created**: When a new symlink is created, it's added to the database
   - **Modified**: When a symlink is modified, the database is updated
   - **Deleted**: When a symlink is deleted, the reference count is decremented
   - **Moved**: Handles both the old location deletion and new location creation

4. **Target Management**:
   - When a symlink is deleted, Alfred checks the reference count
   - If the reference count reaches zero, the target file is deleted
   - This prevents orphaned files while preserving files still in use
   - Configurable delete behavior (files or folders)

5. **Web Interface**:
   - Real-time dashboard with key metrics
   - Historical trend analysis
   - Directory status monitoring
   - Search and filter capabilities
   - Pagination and sorting
   - Detailed symlink information
   - Backup and restore functionality
   - Settings management

6. **Background Processing**:
   - Automatic scanning at configurable intervals
   - Manual scan capability
   - Dry-run mode for testing
   - Comprehensive logging
   - Multi-platform support

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/pukabyte/alfred.git
   cd alfred
   ```
2. Create .env file:
   ```bash
   mv .env.example .env
   ```

3. Create docker-compose.yml:   
   ```bash
   mv docker-compose-example.yml docker-compose.yml
   ```

4. Build and start the container:
   ```bash
   docker-compose up -d
   ```

## Configuration

The container can be configured using environment variables:

- `SYMLINK_DIR`: Directory to monitor for symlinks (default: `/mnt/plex`)
- `TORRENTS_DIR`: Directory containing target files (default: `/mnt/remote/realdebrid/__all__`)
- `DELETE_BEHAVIOR`: Choose between 'files' or 'folders' for deletion (default: 'files')
- `SCAN_INTERVAL`: Background scan interval in minutes (0 to disable, default: 720)

You can set these in your `.env` file or directly in docker-compose.yml.

## Usage

The container runs automatically once started. It will:
1. Perform a healthcheck on the directories
2. Delete files from RD mount that are not referenced by symlinks (requires a mount point that allows deletes eg. zurg)
3. Monitor the specified directory for symlink changes
4. Track all symlinks and their targets
5. Delete target files when their last symlink is removed

### Web Interface

Access the web interface at `http://localhost:5000` to:
- View real-time statistics and metrics
- Monitor directory status
- Search and manage symlinks
- Configure settings
- Create backups and restore from backups
- Run manual scans

### UI Previews

#### Dashboard
![Dashboard](/.github/screenshots/dashboard.png)
The dashboard provides an overview of your symlink system, including real-time statistics, historical trends, and directory status.

#### Symlinks Management
![Symlinks](/.github/screenshots/symlinks.png)
Manage your symlinks with search, filtering, and sorting capabilities. View detailed information about each symlink and its target.

#### Settings
![Settings](/.github/screenshots/settings.png)
Configure your Alfred instance with multiple directory support, scan intervals, and delete behavior settings.

### Command Line Arguments

The script supports the following arguments:
- `--dry-run`: Test without making actual changes
- `--no-confirm`: Skip confirmation prompts
- `--torrents-directories`: Specify directories to check
- `--exclude`: Specify patterns to exclude from processing

## Docker Compose Configuration

```yaml
services:
  alfred:
    restart: unless-stopped
    container_name: alfred
    image: ghcr.io/pukabyte/alfred:latest
    hostname: alfred
    user: "1000:1000"
    environment:
      - TZ=Etc/UTC
    ports:
      - 5000:5000
    networks:
      - saltbox
    labels:
      com.github.saltbox.saltbox_managed: true
    volumes:
      - /opt/alfred:/app/data
      - ${SYMLINK_DIR}:${SYMLINK_DIR}
      - ${TORRENTS_DIR}:${TORRENTS_DIR}
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.