document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('settings-form');
    const saveButton = document.getElementById('save-settings-btn');
    const saveStatus = document.getElementById('save-status');
    const statusMessage = saveStatus.querySelector('.status-message');
    const dirsContainer = document.getElementById('symlink-dirs-container');
    const addDirBtn = document.getElementById('add-symlink-dir');

    // Load current settings
    fetch('/api/settings')
        .then(response => response.json())
        .then(settings => {
            // Clear existing directory inputs
            dirsContainer.innerHTML = '';
            
            // Add directory inputs for each symlink directory
            const symlinkDirs = settings.SYMLINK_DIR ? settings.SYMLINK_DIR.split(',') : [];
            symlinkDirs.forEach(dir => {
                if (dir.trim()) {
                    dirsContainer.appendChild(createDirectoryInput(dir.trim()));
                }
            });

            // If no directories are loaded, add one empty input
            if (symlinkDirs.length === 0) {
                dirsContainer.appendChild(createDirectoryInput());
            }

            document.getElementById('torrent-dirs').value = settings.TORRENTS_DIR || '';
            document.getElementById('delete-behavior').value = settings.DELETE_BEHAVIOR || 'files';
            document.getElementById('scan-interval').value = settings.SCAN_INTERVAL || '720';
        })
        .catch(error => showStatus('error', 'Failed to load settings'));

    // Add new directory input when button is clicked
    addDirBtn.addEventListener('click', () => {
        dirsContainer.appendChild(createDirectoryInput());
    });

    form.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        // Show loading state
        saveButton.classList.add('loading');
        
        // Collect all symlink directories
        const symlinkInputs = dirsContainer.querySelectorAll('input[type="text"]');
        const symlinkDirs = Array.from(symlinkInputs)
            .map(input => input.value.trim())
            .filter(Boolean)
            .join(',');

        const formData = {
            SYMLINK_DIR: symlinkDirs,
            TORRENTS_DIR: document.getElementById('torrent-dirs').value,
            DELETE_BEHAVIOR: document.getElementById('delete-behavior').value,
            SCAN_INTERVAL: document.getElementById('scan-interval').value
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
            // Remove loading state
            saveButton.classList.remove('loading');
        }
    });

    function showStatus(type, message) {
        // Remove previous classes
        saveStatus.classList.remove('success', 'error', 'warning', 'visible', 'animate');
        
        // Add new classes
        saveStatus.classList.add(type, 'visible', 'animate');
        statusMessage.textContent = message;
        
        // Remove animation class after it completes
        setTimeout(() => {
            saveStatus.classList.remove('animate', 'visible');
        }, 3000);
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