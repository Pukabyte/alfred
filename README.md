# Alfred - Symlink Manager

Alfred is a Docker container that monitors symlinks in a specified directory and manages their target files. When a symlink is deleted, Alfred will automatically delete the target file if it's no longer referenced by any other symlinks.

## Features

- Real-time monitoring of symlink changes
- Automatic deletion of unreferenced target files
- Reference counting to prevent premature deletion
- Persistent database to track symlinks and their targets
- Docker container for easy deployment
- Saltbox integration ready

## How It Works

1. **Delete Non-linked files**
   - Scan all current symlinks in symlinks directory
   - Cross reference with realdebrid mount directory
   - Deletes all files that are not currently symlinked 

2. **Symlink Monitoring**:
   - Watches a specified directory for symlink changes
   - Tracks all symlinks and their target files in a SQLite database
   - Maintains a reference count for each target file

3. **Event Handling**:
   - **Created**: When a new symlink is created, it's added to the database
   - **Modified**: When a symlink is modified, the database is updated
   - **Deleted**: When a symlink is deleted, the reference count is decremented
   - **Moved**: Handles both the old location deletion and new location creation

4. **Target Management**:
   - When a symlink is deleted, Alfred checks the reference count
   - If the reference count reaches zero, the target file is deleted
   - This prevents orphaned files while preserving files still in use

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

You can set these in your `.env` file or directly in docker-compose.yml.

## Usage

The container runs automatically once started. It will:
1. Delete files from RD mount that are not referenced by symlinks (requires a mount point that allows deletes eg. zurg)
2. Monitor the specified directory for symlink changes
3. Track all symlinks and their targets
4. Delete target files when their last symlink is removed

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
    networks:
      - saltbox
    labels:
      com.github.saltbox.saltbox_managed: true
    volumes:
      - /opt/alfred:/app/data
      - ${SYMLINK_DIR:-/mnt/plex}:${SYMLINK_DIR:-/mnt/plex}
      - ${TORRENTS_DIR:-/mnt/remote/realdebrid/__all__}:${TORRENTS_DIR:-/mnt/remote/realdebrid/__all__}
      - /etc/localtime:/etc/localtime:ro
```

## Logging

Logs are written to both:
- Console (with color formatting)
- File (`symlink_manager.log`) with rotation (10MB) and retention (10 days)

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details. 