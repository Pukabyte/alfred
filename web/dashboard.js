document.addEventListener('DOMContentLoaded', () => {
    const totalSymlinksSpan = document.getElementById('total-symlinks');
    const uniqueTargetsSpan = document.getElementById('unique-targets');
    const lastScanSpan = document.getElementById('last-scan');
    const totalDeletionsSpan = document.getElementById('total-deletions');
    const runScanButton = document.getElementById('run-scan');
    const dryRunCheckbox = document.getElementById('dry-run');
    const noConfirmCheckbox = document.getElementById('no-confirm');
    const scanStatus = document.getElementById('scan-status');
    
    // Chart elements
    const metricSelect = document.getElementById('metric-select');
    const timeRangeSelect = document.getElementById('time-range-select');
    const ctx = document.getElementById('metrics-chart').getContext('2d');
    
    let metricsChart = null;

    // Initialize chart
    function initChart() {
        metricsChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: [],
                datasets: [{
                    label: 'Total Symlinks',
                    data: [],
                    backgroundColor: 'rgba(0, 123, 255, 0.5)',
                    borderColor: 'rgba(0, 123, 255, 1)',
                    borderWidth: 1,
                    borderRadius: 4,
                    barPercentage: 0.8
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'top',
                    },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        callbacks: {
                            label: function(context) {
                                return `${context.dataset.label}: ${context.parsed.y.toLocaleString()}`;
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: function(value) {
                                return value.toLocaleString();
                            }
                        }
                    },
                    x: {
                        grid: {
                            display: false
                        }
                    }
                },
                animation: {
                    duration: 500
                },
                hover: {
                    mode: 'index',
                    intersect: false
                }
            }
        });
    }

    // Update chart data with loading state
    async function updateChart() {
        const metric = metricSelect.value;
        const timeRange = timeRangeSelect.value;
        
        // Show loading state
        metricsChart.data.labels = [];
        metricsChart.data.datasets[0].data = [];
        metricsChart.update('none');
        
        try {
            const response = await fetch(`${window.location.origin}/api/metrics?metric=${metric}&range=${timeRange}`);
            const data = await response.json();
            
            // Format labels based on time range
            const formattedLabels = data.labels.map(label => {
                if (timeRange === 'daily') {
                    return new Date(label).toLocaleDateString();
                } else if (timeRange === 'weekly') {
                    const [year, week] = label.split('-');
                    return `Week ${week}, ${year}`;
                } else {
                    const [year, month] = label.split('-');
                    return new Date(year, month - 1).toLocaleDateString(undefined, { year: 'numeric', month: 'short' });
                }
            });
            
            metricsChart.data.labels = formattedLabels;
            metricsChart.data.datasets[0].label = getMetricLabel(metric);
            metricsChart.data.datasets[0].data = data.values;
            
            // Update chart colors based on metric
            const colors = getMetricColors(metric);
            metricsChart.data.datasets[0].backgroundColor = colors.background;
            metricsChart.data.datasets[0].borderColor = colors.border;
            
            metricsChart.update();
        } catch (error) {
            console.error('Error fetching metrics:', error);
        }
    }

    function getMetricLabel(metric) {
        const labels = {
            total_symlinks: 'Total Symlinks',
            unique_targets: 'Unique Targets',
            total_deletions: 'Total Deletions'
        };
        return labels[metric] || metric;
    }

    function getMetricColors(metric) {
        const colors = {
            total_symlinks: {
                background: 'rgba(0, 123, 255, 0.5)',
                border: 'rgba(0, 123, 255, 1)'
            },
            unique_targets: {
                background: 'rgba(40, 167, 69, 0.5)',
                border: 'rgba(40, 167, 69, 1)'
            },
            total_deletions: {
                background: 'rgba(220, 53, 69, 0.5)',
                border: 'rgba(220, 53, 69, 1)'
            }
        };
        return colors[metric] || colors.total_symlinks;
    }

    // Event listeners for chart controls
    metricSelect.addEventListener('change', updateChart);
    timeRangeSelect.addEventListener('change', updateChart);

    // Initialize chart and load initial data
    initChart();
    updateChart();

    // Fetch dashboard data
    async function fetchDashboardData() {
        try {
            const response = await fetch('/api/dashboard');
            const data = await response.json();
            updateDashboard(data);
            
            // Fetch and update directory status
            const dirResponse = await fetch('/api/directories');
            const dirData = await dirResponse.json();
            updateDirectoryStatus(dirData);
        } catch (error) {
            console.error('Error fetching dashboard data:', error);
        }
    }

    // Update dashboard statistics
    function updateDashboard(data) {
        // Update stats
        document.getElementById('total-symlinks').textContent = data.total_symlinks;
        document.getElementById('unique-targets').textContent = data.unique_targets;
        document.getElementById('total-deletions').textContent = data.total_deletions;
        
        // Format last scan time
        document.getElementById('last-scan').textContent = formatTimeSince(data.last_scan);
        
        // Update scan results
        const scanResults = data.scan_results;
        document.getElementById('files-checked').textContent = scanResults.files_checked;
        document.getElementById('files-deleted').textContent = scanResults.files_deleted;
        document.getElementById('folders-deleted').textContent = scanResults.folders_deleted;
        
        // Format next scan time
        const nextScanDate = data.next_scan ? new Date(data.next_scan * 1000) : null;
        if (nextScanDate) {
            const now = new Date();
            const diffMinutes = Math.round((nextScanDate - now) / (1000 * 60));
            
            if (diffMinutes <= 0) {
                document.getElementById('next-scan').textContent = 'Due now';
            } else if (diffMinutes < 60) {
                document.getElementById('next-scan').textContent = `${diffMinutes}m`;
            } else {
                const hours = Math.floor(diffMinutes / 60);
                const minutes = diffMinutes % 60;
                document.getElementById('next-scan').textContent = `${hours}h ${minutes}m`;
            }
        } else if (scanResults.scan_interval === 0) {
            document.getElementById('next-scan').textContent = 'Manual only';
        } else {
            document.getElementById('next-scan').textContent = 'Not scheduled';
        }
    }

    // Update directory status
    function updateDirectoryStatus(data) {
        const symlinkDirs = document.getElementById('symlink-directories');
        const torrentDirs = document.getElementById('torrent-directories');
        
        symlinkDirs.innerHTML = '';
        torrentDirs.innerHTML = '';
        
        data.symlink_directories.forEach(dir => {
            const item = createDirectoryItem(dir);
            symlinkDirs.appendChild(item);
        });
        
        data.torrent_directories.forEach(dir => {
            const item = createDirectoryItem(dir);
            torrentDirs.appendChild(item);
        });
    }

    function createDirectoryItem(dir) {
        const div = document.createElement('div');
        div.className = 'directory-item';
        div.innerHTML = `
            <div class="directory-status ${dir.status}"></div>
            <div class="directory-path">${dir.path}</div>
        `;
        return div;
    }

    // Format time since last scan
    function formatTimeSince(timestamp) {
        if (!timestamp) return 'Never';
        
        const seconds = Math.floor((Date.now() - timestamp * 1000) / 1000);
        if (seconds < 60) return `${seconds} seconds ago`;
        if (seconds < 3600) return `${Math.floor(seconds / 60)} minutes ago`;
        if (seconds < 86400) return `${Math.floor(seconds / 3600)} hours ago`;
        return `${Math.floor(seconds / 86400)} days ago`;
    }

    // Handle manual scan
    runScanButton.addEventListener('click', async () => {
        runScanButton.disabled = true;
        scanStatus.style.display = 'block';
        scanStatus.textContent = '';
        
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 300000); // 5 minute timeout
            
            const response = await fetch('/api/scan', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    dry_run: false,
                    no_confirm: true
                }),
                signal: controller.signal
            });

            clearTimeout(timeoutId);

            if (!response.ok) {
                throw new Error(`Server error: ${response.status} ${response.statusText}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let hasOutput = false;
            let lastUpdate = Date.now();
            let progressDots = 0;

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                
                const text = decoder.decode(value);
                if (text.trim()) {
                    scanStatus.textContent += text;
                    scanStatus.scrollTop = scanStatus.scrollHeight;
                    hasOutput = true;
                    lastUpdate = Date.now();
                } else {
                    // Add progress indicator if no new output for 2 seconds
                    const now = Date.now();
                    if (now - lastUpdate > 2000) {
                        progressDots = (progressDots + 1) % 4;
                        const dots = '.'.repeat(progressDots);
                        scanStatus.textContent = scanStatus.textContent.replace(/\.{0,3}$/, dots);
                        lastUpdate = now;
                    }
                }
            }

            if (!hasOutput) {
                throw new Error('No output received from scan');
            }

            // Add a small delay before refreshing dashboard data
            await new Promise(resolve => setTimeout(resolve, 1000));
            
            // Refresh dashboard data after scan
            await fetchDashboardData();
            
            // Keep the scan status visible
            const keepVisibleTime = scanStatus.textContent.includes('Error') ? 60000 : 30000;
            setTimeout(() => {
                scanStatus.style.display = 'none';
            }, keepVisibleTime);
        } catch (error) {
            console.error('Error during scan:', error);
            let errorMessage = error.message;
            
            if (error.name === 'AbortError') {
                errorMessage = 'Scan timed out after 5 minutes. Your library might be too large for the current timeout setting.';
            } else if (error.message === 'Failed to fetch' || error.message.includes('network')) {
                errorMessage = 'Network error: Please check your connection and try again. For large libraries, the scan might take several minutes.';
            }
            
            scanStatus.textContent += `\nError: ${errorMessage}`;
            // Keep error visible longer
            setTimeout(() => {
                scanStatus.style.display = 'none';
            }, 60000); // Show for 1 minute on error
        } finally {
            runScanButton.disabled = false;
        }
    });

    document.getElementById('backup-symlinks').addEventListener('click', function() {
        // Show loading state
        const button = this;
        const originalText = button.innerHTML;
        button.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Creating Backup...';
        button.disabled = true;

        // Make the API request
        fetch('/api/backup-symlinks')
            .then(response => {
                if (!response.ok) {
                    throw new Error('Backup failed');
                }
                return response.blob();
            })
            .then(blob => {
                // Create a download link
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `symlinks_backup_${new Date().toISOString().split('T')[0]}.json`;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
            })
            .catch(error => {
                console.error('Error creating backup:', error);
                alert('Failed to create backup. Please try again.');
            })
            .finally(() => {
                // Reset button state
                button.innerHTML = originalText;
                button.disabled = false;
            });
    });

    // Add restore functionality
    document.getElementById('restore-symlinks').addEventListener('click', function() {
        const fileInput = document.getElementById('restore-file');
        fileInput.click();
    });

    document.getElementById('restore-file').addEventListener('change', function(e) {
        const file = e.target.files[0];
        if (!file) return;
        
        // Show loading state
        const button = document.getElementById('restore-symlinks');
        const originalText = button.innerHTML;
        button.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Restoring...';
        button.disabled = true;
        
        // Create form data
        const formData = new FormData();
        formData.append('file', file);
        
        // Make the API request
        fetch('/api/restore-symlinks', {
            method: 'POST',
            body: formData
        })
        .then(response => {
            if (!response.ok) {
                return response.json().then(data => {
                    throw new Error(data.error || 'Restore failed');
                });
            }
            return response.json();
        })
        .then(data => {
            let message = `Successfully restored ${data.restored_count} symlinks`;
            if (data.skipped_count > 0) {
                message += `\nSkipped ${data.skipped_count} symlinks`;
                if (data.errors && data.errors.length > 0) {
                    message += '\n\nErrors:\n' + data.errors.join('\n');
                }
            }
            alert(message);
            // Refresh the dashboard data
            fetchDashboardData();
        })
        .catch(error => {
            console.error('Error restoring backup:', error);
            alert(`Failed to restore backup: ${error.message}`);
        })
        .finally(() => {
            // Reset button state and clear file input
            button.innerHTML = originalText;
            button.disabled = false;
            e.target.value = '';
        });
    });

    // Initial data fetch
    fetchDashboardData();
}); 