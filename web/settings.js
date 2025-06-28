document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('settings-form');
    const saveButton = document.getElementById('save-settings-btn');
    const saveStatus = document.getElementById('save-status');
    const statusMessage = saveStatus.querySelector('.status-message');
    const symlinkDirsContainer = document.getElementById('symlink-dirs-container');
    const torrentDirsContainer = document.getElementById('torrent-dirs-container');
    const addSymlinkDirBtn = document.getElementById('add-symlink-dir');
    const addTorrentDirBtn = document.getElementById('add-torrent-dir');

    // Load current settings
    fetch('/api/settings')
        .then(response => response.json())
        .then(settings => {
            // Clear existing directory inputs
            symlinkDirsContainer.innerHTML = '';
            torrentDirsContainer.innerHTML = '';
            
            // Add directory inputs for each symlink directory
            const symlinkDirs = settings.SYMLINK_DIR ? settings.SYMLINK_DIR.split(',') : [];
            symlinkDirs.forEach(dir => {
                if (dir.trim()) {
                    symlinkDirsContainer.appendChild(createDirectoryInput(dir.trim()));
                }
            });

            // Add directory inputs for each torrent directory
            const torrentDirs = settings.TORRENTS_DIR ? settings.TORRENTS_DIR.split(',') : [];
            torrentDirs.forEach(dir => {
                if (dir.trim()) {
                    torrentDirsContainer.appendChild(createDirectoryInput(dir.trim()));
                }
            });

            // If no directories are loaded, add one empty input for each
            if (symlinkDirs.length === 0) {
                symlinkDirsContainer.appendChild(createDirectoryInput());
            }
            if (torrentDirs.length === 0) {
                torrentDirsContainer.appendChild(createDirectoryInput());
            }

            document.getElementById('delete-behavior').value = settings.DELETE_BEHAVIOR || 'files';
            document.getElementById('scan-interval').value = settings.SCAN_INTERVAL || '720';
            document.getElementById('pending-deletion-grace').value = settings.PENDING_DELETION_GRACE_SECONDS || '60';
            document.getElementById('dry-run').value = (settings.DRY_RUN === 'true' || settings.DRY_RUN === true) ? 'true' : 'false';
            document.getElementById('run-on-startup').value = (settings.RUN_ON_STARTUP === 'false' || settings.RUN_ON_STARTUP === false) ? 'false' : 'true';
        })
        .catch(error => showStatus('error', 'Failed to load settings'));

    // Add new directory input when buttons are clicked
    addSymlinkDirBtn.addEventListener('click', () => {
        symlinkDirsContainer.appendChild(createDirectoryInput());
    });

    addTorrentDirBtn.addEventListener('click', () => {
        torrentDirsContainer.appendChild(createDirectoryInput());
    });

    form.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        // Show loading state
        saveButton.classList.add('loading');
        
        // Collect all symlink directories
        const symlinkInputs = symlinkDirsContainer.querySelectorAll('input[type="text"]');
        const symlinkDirs = Array.from(symlinkInputs)
            .map(input => input.value.trim())
            .filter(Boolean)
            .join(',');

        // Collect all torrent directories
        const torrentInputs = torrentDirsContainer.querySelectorAll('input[type="text"]');
        const torrentDirs = Array.from(torrentInputs)
            .map(input => input.value.trim())
            .filter(Boolean)
            .join(',');

        const formData = {
            SYMLINK_DIR: symlinkDirs,
            TORRENTS_DIR: torrentDirs,
            DELETE_BEHAVIOR: document.getElementById('delete-behavior').value,
            SCAN_INTERVAL: document.getElementById('scan-interval').value,
            PENDING_DELETION_GRACE_SECONDS: document.getElementById('pending-deletion-grace').value,
            DRY_RUN: document.getElementById('dry-run').value,
            RUN_ON_STARTUP: document.getElementById('run-on-startup').value
        };

        try {
            // Save settings
            const saveResponse = await fetch('/api/settings', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(formData)
            });

            const saveData = await saveResponse.json();

            if (saveResponse.ok) {
                // If save was successful, reload settings
                const reloadResponse = await fetch('/api/settings/reload', {
                    method: 'POST'
                });

                const reloadData = await reloadResponse.json();

                if (reloadResponse.ok) {
                    showStatus('success', 'Settings saved and reloaded successfully');
                } else {
                    showStatus('warning', 'Settings saved but reload failed: ' + (reloadData.error || 'Unknown error'));
                }
            } else {
                showStatus('error', saveData.error || 'Failed to save settings');
            }
        } catch (error) {
            showStatus('error', 'Failed to save settings');
            console.error('Error:', error);
        } finally {
            saveButton.classList.remove('loading');
        }
    });

    function showStatus(type, message) {
        const icon = saveStatus.querySelector('.status-icon');
        const messageSpan = saveStatus.querySelector('.status-message');
        
        saveStatus.className = 'save-status visible ' + type;
        icon.className = 'status-icon fa-solid ' + (type === 'success' ? 'fa-check' : type === 'error' ? 'fa-times' : 'fa-exclamation-triangle');
        messageSpan.textContent = message;
        
        // Hide status after 5 seconds
        setTimeout(() => {
            saveStatus.className = 'save-status';
        }, 5000);
    }
});

function createDirectoryInput(value = '') {
    const container = document.createElement('div');
    container.className = 'directory-input-group';

    const input = document.createElement('input');
    input.type = 'text';
    input.value = value;
    input.placeholder = 'Enter directory path';

    const removeBtn = document.createElement('button');
    removeBtn.className = 'remove-dir-btn';
    removeBtn.type = 'button';
    removeBtn.innerHTML = '<i class="fa-solid fa-times"></i>';
    removeBtn.onclick = () => container.remove();

    container.appendChild(input);
    container.appendChild(removeBtn);
    return container;
} 