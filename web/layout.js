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

// Function to save sidebar state
function saveSidebarState(isCollapsed) {
    localStorage.setItem('sidebarCollapsed', isCollapsed);
}

// Function to load sidebar state
function loadSidebarState() {
    const isCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';
    const sidebar = document.querySelector('.sidebar');
    if (isCollapsed) {
        sidebar.classList.add('collapsed');
    } else {
        sidebar.classList.remove('collapsed');
    }
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
    
    // Add sidebar toggle functionality
    const sidebar = document.querySelector('.sidebar');
    const toggleButton = document.getElementById('sidebar-toggle');
    
    // Load saved state
    loadSidebarState();
    
    toggleButton.addEventListener('click', () => {
        sidebar.classList.toggle('collapsed');
        // Save new state
        saveSidebarState(sidebar.classList.contains('collapsed'));
    });
    
    // Refresh directory status every 30 seconds
    setInterval(updateDirectoryStatus, 30000);
});

// Global dark mode logic
function setDarkMode(enabled) {
    if (enabled) {
        document.body.classList.add('dark-mode');
        // Switch logo to dark
        const logoImg = document.querySelector('.logo-img');
        if (logoImg) logoImg.src = '/assets/logo-dark.png';
    } else {
        document.body.classList.remove('dark-mode');
        // Switch logo to light
        const logoImg = document.querySelector('.logo-img');
        if (logoImg) logoImg.src = '/assets/logo.png';
    }
    localStorage.setItem('alfred-dark-mode', enabled ? 'true' : 'false');
}

function getDarkMode() {
    return localStorage.getItem('alfred-dark-mode') === 'true';
}

// Listen for changes to dark mode in other tabs
window.addEventListener('storage', (event) => {
    if (event.key === 'alfred-dark-mode') {
        setDarkMode(event.newValue === 'true');
    }
});

// Export for use in other scripts
window.setDarkMode = setDarkMode;
window.getDarkMode = getDarkMode; 