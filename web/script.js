document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('search-input');
    const symlinksBody = document.getElementById('symlinks-body');
    const totalSymlinksSpan = document.getElementById('total-symlinks');
    const uniqueTargetsSpan = document.getElementById('unique-targets');
    const rowsPerPageSelect = document.getElementById('rows-per-page');
    const prevPageButton = document.getElementById('prev-page');
    const nextPageButton = document.getElementById('next-page');
    const pageInfo = document.getElementById('page-info');
    const modal = document.getElementById('symlink-modal');
    const closeModal = document.querySelector('.close-modal');
    const modalSymlink = document.getElementById('modal-symlink');
    const modalTarget = document.getElementById('modal-target');
    const modalRefCount = document.getElementById('modal-ref-count');

    let originalData = [];
    let symlinksData = [];
    let searchTimeout;
    let currentPage = 1;
    let rowsPerPage = parseInt(rowsPerPageSelect.value);
    let currentSort = { column: null, ascending: true };

    // Modal functionality
    function showModal() {
        modal.style.display = 'block';
    }

    function hideModal() {
        modal.style.display = 'none';
    }

    closeModal.addEventListener('click', hideModal);
    window.addEventListener('click', (event) => {
        if (event.target === modal) {
            hideModal();
        }
    });

    // Fetch data from the backend
    async function fetchSymlinks() {
        const loadingAnimation = document.getElementById('loading-animation');
        try {
            loadingAnimation.classList.add('visible');
            const response = await fetch(`${window.location.origin}/api/symlinks`);
            const data = await response.json();
            originalData = data;
            
            // Store current search term
            const currentSearchTerm = searchInput.value;
            
            // Apply current search filter if exists
            if (currentSearchTerm) {
                symlinksData = data.filter(item => 
                    item.symlink.toLowerCase().includes(currentSearchTerm.toLowerCase()) ||
                    item.target.toLowerCase().includes(currentSearchTerm.toLowerCase())
                );
            } else {
                symlinksData = [...data];
            }
            
            // Re-apply current sort if exists
            if (currentSort.column) {
                symlinksData.sort((a, b) => {
                    let valueA = a[currentSort.column];
                    let valueB = b[currentSort.column];

                    if (currentSort.column === 'ref_count') {
                        valueA = parseInt(valueA);
                        valueB = parseInt(valueB);
                    } else {
                        valueA = valueA.toLowerCase();
                        valueB = valueB.toLowerCase();
                    }

                    if (valueA < valueB) return currentSort.ascending ? -1 : 1;
                    if (valueA > valueB) return currentSort.ascending ? 1 : -1;
                    return 0;
                });
            }
            
            updateStats();
            renderTable();
        } catch (error) {
            console.error('Error fetching symlinks:', error);
        } finally {
            loadingAnimation.classList.remove('visible');
        }
    }

    // Update statistics
    function updateStats() {
        const totalSymlinks = symlinksData.length;
        const uniqueTargets = new Set(symlinksData.map(item => item.target)).size;

        totalSymlinksSpan.textContent = totalSymlinks;
        uniqueTargetsSpan.textContent = uniqueTargets;
    }

    // Calculate pagination
    function getPaginatedData(data) {
        const start = (currentPage - 1) * rowsPerPage;
        const end = start + rowsPerPage;
        return data.slice(start, end);
    }

    // Update pagination controls
    function updatePaginationControls() {
        const totalPages = Math.ceil(symlinksData.length / rowsPerPage);
        pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
        prevPageButton.disabled = currentPage === 1;
        nextPageButton.disabled = currentPage === totalPages;
    }

    // Sort data
    function sortData(column) {
        if (currentSort.column === column) {
            currentSort.ascending = !currentSort.ascending;
        } else {
            currentSort.column = column;
            currentSort.ascending = true;
        }

        symlinksData.sort((a, b) => {
            let valueA = a[column];
            let valueB = b[column];

            if (column === 'ref_count') {
                valueA = parseInt(valueA);
                valueB = parseInt(valueB);
            } else {
                valueA = valueA.toLowerCase();
                valueB = valueB.toLowerCase();
            }

            if (valueA < valueB) return currentSort.ascending ? -1 : 1;
            if (valueA > valueB) return currentSort.ascending ? 1 : -1;
            return 0;
        });

        currentPage = 1;
        renderTable();
    }

    // Filter data
    function filterData(searchTerm) {
        if (!searchTerm) {
            symlinksData = [...originalData];
        } else {
            searchTerm = searchTerm.toLowerCase();
            symlinksData = originalData.filter(item => 
                item.symlink.toLowerCase().includes(searchTerm) ||
                item.target.toLowerCase().includes(searchTerm)
            );
        }
        currentPage = 1;
        updateStats();
        renderTable();
    }

    // Render the table with pagination
    function renderTable() {
        symlinksBody.innerHTML = '';
        const fragment = document.createDocumentFragment();
        const paginatedData = getPaginatedData(symlinksData);
        
        // Update header sort indicators
        document.querySelectorAll('.sort-indicator').forEach(indicator => {
            const column = indicator.getAttribute('data-column');
            if (column === currentSort.column) {
                indicator.textContent = currentSort.ascending ? ' ↑' : ' ↓';
            } else {
                indicator.textContent = '';
            }
        });

        paginatedData.forEach(item => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${item.symlink}</td>
                <td>${item.target}</td>
                <td>${item.ref_count}</td>
                <td>
                    <button class="action-button view-button" onclick="viewSymlink('${item.symlink}')">View</button>
                    <button class="action-button delete-button" onclick="deleteSymlink('${item.symlink}')">Delete</button>
                </td>
            `;
            fragment.appendChild(row);
        });
        
        symlinksBody.appendChild(fragment);
        updatePaginationControls();
    }

    // Event listeners for pagination
    rowsPerPageSelect.addEventListener('change', () => {
        rowsPerPage = parseInt(rowsPerPageSelect.value);
        currentPage = 1;
        renderTable();
    });

    prevPageButton.addEventListener('click', () => {
        if (currentPage > 1) {
            currentPage--;
            renderTable();
        }
    });

    nextPageButton.addEventListener('click', () => {
        const totalPages = Math.ceil(symlinksData.length / rowsPerPage);
        if (currentPage < totalPages) {
            currentPage++;
            renderTable();
        }
    });

    // Debounced search functionality
    searchInput.addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            filterData(e.target.value);
        }, 300);
    });

    // Add click handlers for sorting
    document.querySelectorAll('.sortable').forEach(header => {
        header.addEventListener('click', () => {
            const column = header.getAttribute('data-column');
            sortData(column);
        });
    });

    // View symlink details
    window.viewSymlink = async (symlink) => {
        try {
            const response = await fetch(`${window.location.origin}/api/symlinks/${encodeURIComponent(symlink)}`);
            const data = await response.json();
            
            modalSymlink.textContent = data.symlink;
            modalTarget.textContent = data.target;
            modalRefCount.textContent = data.ref_count;
            
            showModal();
        } catch (error) {
            console.error('Error viewing symlink:', error);
        }
    };

    // Delete symlink
    window.deleteSymlink = async (symlink) => {
        if (confirm(`Are you sure you want to delete the symlink: ${symlink}?`)) {
            try {
                const response = await fetch(`${window.location.origin}/api/symlinks/${encodeURIComponent(symlink)}`, {
                    method: 'DELETE'
                });
                if (response.ok) {
                    await fetchSymlinks();
                } else {
                    alert('Error deleting symlink');
                }
            } catch (error) {
                console.error('Error deleting symlink:', error);
            }
        }
    };

    // Initial data fetch
    fetchSymlinks();

    // Refresh data every 30 seconds
    setInterval(fetchSymlinks, 30000);

    // Add global error handler for fetch calls
    window.addEventListener('unhandledrejection', function(event) {
        console.error('Unhandled promise rejection:', event.reason);
    });
}); 