// Function to fetch and update directory status
async function updateDirectoryStatus() {
    try {
        const response = await fetch('/api/directories');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        
        // Update symlink directories
        const symlinkDirsList = document.getElementById('symlink-directories');
        if (symlinkDirsList) {
            symlinkDirsList.innerHTML = '';
            if (data.symlink_directories && Array.isArray(data.symlink_directories)) {
                data.symlink_directories.forEach(dir => {
                    const item = createDirectoryItem(dir);
                    symlinkDirsList.appendChild(item);
                });
            }
        }
        
        // Update torrent directories
        const torrentDirsList = document.getElementById('torrent-directories');
        if (torrentDirsList) {
            torrentDirsList.innerHTML = '';
            if (data.torrent_directories && Array.isArray(data.torrent_directories)) {
                data.torrent_directories.forEach(dir => {
                    const item = createDirectoryItem(dir);
                    torrentDirsList.appendChild(item);
                });
            }
        }
    } catch (error) {
        console.error('Error fetching directory status:', error);
    }
}

// Function to create a directory item element
function createDirectoryItem(dir) {
    const item = document.createElement('div');
    item.className = 'directory-item';
    
    const status = document.createElement('div');
    status.className = `directory-status ${dir.status || 'down'}`;
    item.appendChild(status);
    
    const path = document.createElement('span');
    path.className = 'directory-path';
    path.textContent = dir.path || 'Unknown path';
    item.appendChild(path);
    
    return item;
}

// Update active link in sidebar based on current page
function updateActiveSidebarLink() {
    const currentPath = window.location.pathname;
    document.querySelectorAll('.sidebar-link').forEach(link => {
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('active');
        } else {
            link.classList.remove('active');
        }
    });
}

// Initialize sidebar functionality
document.addEventListener('DOMContentLoaded', () => {
    updateDirectoryStatus();
    updateActiveSidebarLink();
    
    // Refresh directory status every 30 seconds
    setInterval(updateDirectoryStatus, 30000);
}); 