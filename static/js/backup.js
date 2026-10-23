// static/js/backup.js

async function triggerDumpBackup() {
    const btn = document.getElementById('backup-db-btn');
    const textSpan = document.getElementById('backup-btn-text');
    if (!btn || btn.disabled) return;
    
    // Disable button & show progress
    btn.disabled = true;
    btn.style.opacity = '0.7';
    btn.style.cursor = 'not-allowed';
    textSpan.innerText = 'Backing up...';
    
    try {
        const response = await fetch('/mutual-funds/backup-db', {
            method: 'POST'
        });
        const data = await response.json();
        if (data.success) {
            alert('Database backup completed successfully and saved to OneDrive dumps folder!\n\nDetails:\n' + data.details);
            textSpan.innerText = 'Backup Complete!';
            setTimeout(() => {
                textSpan.innerText = 'Backup Database';
                btn.disabled = false;
                btn.style.opacity = '1';
                btn.style.cursor = 'pointer';
            }, 3000);
        } else {
            alert('Backup failed: ' + (data.error || 'Unknown error'));
            textSpan.innerText = 'Backup Failed';
            btn.disabled = false;
            btn.style.opacity = '1';
            btn.style.cursor = 'pointer';
        }
    } catch (err) {
        alert('Network error trying to trigger backup: ' + err);
        textSpan.innerText = 'Backup Error';
        btn.disabled = false;
        btn.style.opacity = '1';
        btn.style.cursor = 'pointer';
    }
}

// Global page load duration tracker
window.addEventListener('load', () => {
    // Add page load time display
    setTimeout(() => {
        let loadTime = 0;
        if (window.performance) {
            if (window.performance.getEntriesByType) {
                const navEntries = window.performance.getEntriesByType('navigation');
                if (navEntries.length > 0) {
                    loadTime = navEntries[0].duration / 1000;
                }
            }
            if (loadTime === 0 && window.performance.timing) {
                const timing = window.performance.timing;
                loadTime = (timing.loadEventEnd - timing.navigationStart) / 1000;
            }
        }
        
        if (loadTime > 0) {
            const backupBtn = document.getElementById('backup-db-btn');
            if (backupBtn && !document.getElementById('page-load-badge')) {
                const badge = document.createElement('div');
                badge.id = 'page-load-badge';
                badge.style.cssText = 'font-size: 11px; font-weight: 500; color: #94a3b8; background: rgba(30, 41, 59, 0.45); border: 1px solid rgba(255,255,255,0.08); padding: 4px 8px; border-radius: 6px; display: inline-flex; align-items: center; gap: 4px; font-family: inherit; font-style: normal; height: 28px; box-sizing: border-box;';
                badge.innerHTML = `
                    <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                    </svg>
                    <span>${loadTime.toFixed(2)}s</span>
                `;
                backupBtn.parentNode.insertBefore(badge, backupBtn);
            }
        }
    }, 50);
});

