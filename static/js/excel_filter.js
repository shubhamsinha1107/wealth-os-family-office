// static/js/excel_filter.js

class TableFilterSort {
    constructor(tableElement) {
        this.table = tableElement;
        this.tbody = this.table.querySelector('tbody');
        this.headers = Array.from(this.table.querySelectorAll('thead th'));
        this.rows = Array.from(this.tbody.querySelectorAll('tr'));
        this.filters = {}; // { colIndex: Set of checked values }
        this.sortState = { colIndex: -1, dir: 'none' };
        
        // Edge case: empty placeholder row
        if (this.rows.length === 1 && this.rows[0].querySelector('td[colspan]')) {
            return;
        }
        
        this.init();
    }
    
    init() {
        // Prepare headers (wrap header text with a clickable wrapper)
        this.headers.forEach((th, idx) => {
            const headerText = th.textContent.trim().toLowerCase();
            // Skip Actions or empty headers
            if (headerText === 'actions' || headerText === '') return;
            
            th.style.position = 'relative';
            const originalContent = th.innerHTML;
            th.innerHTML = '';
            
            const headerWrapper = document.createElement('div');
            headerWrapper.className = 'excel-filter-header';
            headerWrapper.innerHTML = `
                <span>${originalContent}</span>
                <span class="excel-filter-icon">▼</span>
            `;
            th.appendChild(headerWrapper);
            
            // Create dropdown
            const dropdown = document.createElement('div');
            dropdown.className = 'excel-filter-dropdown';
            dropdown.id = `filter-dropdown-${idx}`;
            dropdown.innerHTML = `
                <div class="filter-sort-option sort-asc">
                    <span style="font-weight: bold; margin-right: 8px;">↑</span> Sort A to Z / Smallest to Largest
                </div>
                <div class="filter-sort-option sort-desc">
                    <span style="font-weight: bold; margin-right: 8px;">↓</span> Sort Z to A / Largest to Smallest
                </div>
                <div class="filter-divider"></div>
                <div class="filter-search-container">
                    <input type="text" class="filter-search-input" placeholder="Search values...">
                </div>
                <div class="filter-options-list"></div>
                <div class="filter-actions">
                    <button class="filter-btn-clear">Clear</button>
                    <button class="filter-btn-apply">Apply</button>
                </div>
            `;
            th.appendChild(dropdown);
            
            // Event Listeners
            headerWrapper.addEventListener('click', (e) => {
                e.stopPropagation();
                // Close other dropdowns
                document.querySelectorAll('.excel-filter-dropdown').forEach(d => {
                    if (d !== dropdown) d.classList.remove('show');
                });
                // Toggle this dropdown
                dropdown.classList.toggle('show');
                if (dropdown.classList.contains('show')) {
                    this.populateOptions(idx);
                }
            });
            
            // Prevent dropdown click from closing itself
            dropdown.addEventListener('click', (e) => e.stopPropagation());
            
            // Sort buttons
            dropdown.querySelector('.sort-asc').addEventListener('click', () => {
                this.sortColumn(idx, 'asc');
                dropdown.classList.remove('show');
            });
            dropdown.querySelector('.sort-desc').addEventListener('click', () => {
                this.sortColumn(idx, 'desc');
                dropdown.classList.remove('show');
            });
            
            // Search field
            const searchInput = dropdown.querySelector('.filter-search-input');
            searchInput.addEventListener('input', () => {
                const term = searchInput.value.toLowerCase();
                const items = dropdown.querySelectorAll('.filter-option-item');
                items.forEach(item => {
                    const label = item.textContent.trim().toLowerCase();
                    if (label.includes(term)) {
                        item.style.display = 'flex';
                    } else {
                        item.style.display = 'none';
                    }
                });
            });
            
            // Apply & Clear
            dropdown.querySelector('.filter-btn-apply').addEventListener('click', () => {
                this.applyFilterForColumn(idx);
                dropdown.classList.remove('show');
            });
            dropdown.querySelector('.filter-btn-clear').addEventListener('click', () => {
                this.clearFilterForColumn(idx);
                dropdown.classList.remove('show');
            });
        });
        
        // Close dropdowns on document click
        document.addEventListener('click', () => {
            document.querySelectorAll('.excel-filter-dropdown').forEach(d => d.classList.remove('show'));
        });
    }
    
    populateOptions(colIdx) {
        const dropdown = this.table.querySelector(`#filter-dropdown-${colIdx}`);
        const listContainer = dropdown.querySelector('.filter-options-list');
        listContainer.innerHTML = '';
        
        // Reset search field
        dropdown.querySelector('.filter-search-input').value = '';
        
        // Find all unique values from rows
        const values = new Set();
        this.rows.forEach(row => {
            const cell = row.children[colIdx];
            if (cell) {
                values.add(cell.textContent.trim());
            }
        });
        
        const sortedValues = Array.from(values).sort((a, b) => {
            // Special sorting logic for numeric columns (Sum Assured - Index 5)
            const isNumeric = colIdx === 5;
            if (isNumeric) {
                const parse = (s) => parseFloat(s.replace(/[^\d.-]/g, '')) || 0;
                return parse(a) - parse(b);
            }
            return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
        });
        
        // Add "Select All" option
        const selectAllItem = document.createElement('div');
        selectAllItem.className = 'filter-option-item';
        selectAllItem.innerHTML = `
            <input type="checkbox" class="filter-option-checkbox select-all-chk" checked>
            <span><strong>(Select All)</strong></span>
        `;
        listContainer.appendChild(selectAllItem);
        
        const chkBoxes = [];
        const activeFilter = this.filters[colIdx];
        
        sortedValues.forEach(val => {
            const isChecked = !activeFilter || activeFilter.has(val);
            const item = document.createElement('div');
            item.className = 'filter-option-item';
            item.innerHTML = `
                <input type="checkbox" class="filter-option-checkbox value-chk" data-value="${val}" ${isChecked ? 'checked' : ''}>
                <span>${val}</span>
            `;
            listContainer.appendChild(item);
            chkBoxes.push(item.querySelector('.value-chk'));
        });
        
        // Sync Select All checkbox
        const selectAllChk = selectAllItem.querySelector('.select-all-chk');
        selectAllChk.checked = chkBoxes.every(chk => chk.checked);
        
        selectAllChk.addEventListener('change', () => {
            const state = selectAllChk.checked;
            chkBoxes.forEach(chk => chk.checked = state);
        });
        
        chkBoxes.forEach(chk => {
            chk.addEventListener('change', () => {
                selectAllChk.checked = chkBoxes.every(c => c.checked);
            });
        });
    }
    
    applyFilterForColumn(colIdx) {
        const dropdown = this.table.querySelector(`#filter-dropdown-${colIdx}`);
        const checkedBoxes = Array.from(dropdown.querySelectorAll('.value-chk:checked'));
        const allBoxes = Array.from(dropdown.querySelectorAll('.value-chk'));
        
        const headerWrapper = this.headers[colIdx].querySelector('.excel-filter-header');
        
        if (checkedBoxes.length === allBoxes.length) {
            // No filter applied (all are selected)
            delete this.filters[colIdx];
            headerWrapper.classList.remove('active-filter');
            headerWrapper.querySelector('.excel-filter-icon').textContent = '▼';
        } else {
            // Apply filter
            const selectedValues = new Set(checkedBoxes.map(chk => chk.getAttribute('data-value')));
            this.filters[colIdx] = selectedValues;
            headerWrapper.classList.add('active-filter');
            headerWrapper.querySelector('.excel-filter-icon').textContent = '▼ (filtered)';
        }
        
        this.runFiltering();
    }
    
    clearFilterForColumn(colIdx) {
        delete this.filters[colIdx];
        const headerWrapper = this.headers[colIdx].querySelector('.excel-filter-header');
        headerWrapper.classList.remove('active-filter');
        headerWrapper.querySelector('.excel-filter-icon').textContent = '▼';
        this.runFiltering();
    }
    
    runFiltering() {
        this.rows.forEach(row => {
            let matches = true;
            
            // Row must match all active column filters
            for (const colIdx in this.filters) {
                const activeFilter = this.filters[colIdx];
                const cell = row.children[colIdx];
                const cellVal = cell ? cell.textContent.trim() : '';
                if (!activeFilter.has(cellVal)) {
                    matches = false;
                    break;
                }
            }
            
            if (matches) {
                row.style.display = '';
            } else {
                row.style.display = 'none';
            }
        });
    }
    
    sortColumn(colIdx, dir) {
        this.sortState = { colIndex: colIdx, dir: dir };
        
        // Reset other sort indicators
        this.headers.forEach((th, idx) => {
            const headerWrapper = th.querySelector('.excel-filter-header');
            if (headerWrapper) {
                const icon = headerWrapper.querySelector('.excel-filter-icon');
                const isFiltered = this.filters[idx];
                icon.textContent = isFiltered ? '▼ (filtered)' : '▼';
            }
        });
        
        const headerWrapper = this.headers[colIdx].querySelector('.excel-filter-header');
        const icon = headerWrapper.querySelector('.excel-filter-icon');
        const isFiltered = this.filters[colIdx];
        icon.textContent = dir === 'asc' ? '▲' : '▼';
        if (isFiltered) icon.textContent += ' (filtered)';
        
        const isNumeric = colIdx === 5; // Sum Assured
        
        this.rows.sort((a, b) => {
            let valA = a.children[colIdx].textContent.trim();
            let valB = b.children[colIdx].textContent.trim();
            
            if (isNumeric) {
                const parseNum = (s) => parseFloat(s.replace(/[^\d.-]/g, '')) || 0;
                return dir === 'asc' ? parseNum(valA) - parseNum(valB) : parseNum(valB) - parseNum(valA);
            } else {
                // Check if it's "File No" (format: T-294, etc.) -> sort numerically by the suffix
                const isFileNo = colIdx === 0;
                if (isFileNo) {
                    const parseFileNo = (s) => parseInt(s.replace(/[^\d]/g, '')) || 0;
                    return dir === 'asc' ? parseFileNo(valA) - parseFileNo(valB) : parseFileNo(valB) - parseFileNo(valA);
                }
                return dir === 'asc' 
                    ? valA.localeCompare(valB, undefined, { numeric: true, sensitivity: 'base' })
                    : valB.localeCompare(valA, undefined, { numeric: true, sensitivity: 'base' });
            }
        });
        
        // Re-append sorted rows to the table body
        this.rows.forEach(row => this.tbody.appendChild(row));
    }
}
