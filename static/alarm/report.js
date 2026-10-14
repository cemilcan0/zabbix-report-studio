const API_BASE = '/alarm/api';
Chart.register(ChartDataLabels);

const PROBLEM_CATEGORIES = {
    'CPU': { keywords: ['cpu'], color: '#f97316' },
    'Memory': { keywords: ['memory'], color: '#a855f7' },
    'Ping': { keywords: ['ping'], color: '#ef4444' },
    'Space': { keywords: ['space'], color: '#eab308' },
    'URL/Web': { keywords: ['url'], color: '#14b8a6' },
    'Restart': { keywords: ['restart'], color: '#8b5cf6' },
    'Other': { keywords: [], color: '#64748b' }
};

// Shift values are stored as 'Mesai İçi' / 'Mesai Dışı' (also in the cache DB);
// only the label shown to the user is translated.
const mesaiLabel = v => t(v === 'Mesai İçi' ? 'Business hours' : 'After hours');
const pctText = v => (I18N.lang === 'tr' ? `%${v}` : `${v}%`);
const durUnits = () => (I18N.lang === 'tr' ? { d: 'g', h: 's', m: 'dk', s: 'sn' } : { d: 'd', h: 'h', m: 'm', s: 's' });

function categorizeProblem(problemName) {
    const lower = problemName.toLowerCase();
    for (const [cat, info] of Object.entries(PROBLEM_CATEGORIES)) {
        if (cat === 'Other') continue;
        if (info.keywords.some(kw => lower.includes(kw))) return cat;
    }
    return 'Other';
}

const reportState = {
    hours: { start: 8, end: 18, days: [0, 1, 2, 3, 4] },   // business hours (from /api/business-hours)
    sessionId: '',
    zabbixUrl: '',
    hostGroups: [],
    selectedGroups: new Set(),
    lastChipIndex: -1,
    selectedSeverities: new Set(['5', '4', '3', '2']),
    reportData: [],
    filteredData: [],
    excludedCombinations: new Set(),
    excludedDevices: new Set(),
    severityChart: null,
    hostProblemChart: null,
    topDevicesChart: null,
    mesaiChart: null,
    currentTop10: [],
    currentTopDevices: [],
    hostSearchFilter: '',
    _toastTimer: null,
    // Detail table filters
    detailFilters: {
        severity: new Set(),
        host: new Set(),
        mesai: new Set(),
        category: new Set()
    },
    problemSearchText: '',
    openDropdown: null
};

const SEVERITY_MAP = {
    '0': { name: 'Not Classified', class: 'not-classified', color: '#97AAB3', rowClass: 'severity-row-notclassified' },
    '1': { name: 'Information', class: 'info', color: '#7499FF', rowClass: 'severity-row-info' },
    '2': { name: 'Warning', class: 'warning', color: '#FFC800', rowClass: 'severity-row-warning' },
    '3': { name: 'Average', class: 'average', color: '#FFAA00', rowClass: 'severity-row-average' },
    '4': { name: 'High', class: 'high', color: '#FF8800', rowClass: 'severity-row-high' },
    '5': { name: 'Disaster', class: 'disaster', color: '#FF4444', rowClass: 'severity-row-disaster' }
};

// ===== Initialize =====
document.addEventListener('DOMContentLoaded', async () => {
    initDatePickers();
    initSeverityFilter();
    initExcludedPopup();
    initExcludedDevicesPopup();
    initDetailFilters();

    document.getElementById('generateReportBtn').addEventListener('click', generateReport);
    document.getElementById('exportReportBtn').addEventListener('click', exportReport);
    document.getElementById('exportPdfBtn').addEventListener('click', exportPDF);
    document.getElementById('reportLogoutBtn').addEventListener('click', handleReportLogout);
    document.getElementById('clearCacheBtn').addEventListener('click', handleClearCache);
    document.getElementById('compareBtn').addEventListener('click', comparePrevious);

    document.getElementById('filtersToggle').addEventListener('click', () => {
        document.querySelector('.report-filters-panel').classList.toggle('collapsed');
    });

    document.getElementById('chartExpandBtn').addEventListener('click', () => {
        const container = document.getElementById('hostProblemDistribution');
        const btn = document.getElementById('chartExpandBtn');
        const isExpanded = container.classList.toggle('chart-expanded');
        btn.querySelector('.icon-expand').style.display = isExpanded ? 'none' : '';
        btn.querySelector('.icon-collapse').style.display = isExpanded ? '' : 'none';
        btn.title = isExpanded ? 'Collapse' : 'Expand';
        setCompactTable(container, isExpanded);
        setTimeout(() => { if (reportState.hostProblemChart) reportState.hostProblemChart.resize(); }, 450);
    });

    document.getElementById('reportHostSearch').addEventListener('input', (e) => {
        reportState.hostSearchFilter = e.target.value.toLowerCase();
        renderHostGroupChips();
    });

    document.getElementById('chartExpandDevicesBtn').addEventListener('click', () => {
        const container = document.getElementById('topDevicesDistribution');
        const btn = document.getElementById('chartExpandDevicesBtn');
        const isExpanded = container.classList.toggle('chart-expanded');
        btn.querySelector('.icon-expand').style.display = isExpanded ? 'none' : '';
        btn.querySelector('.icon-collapse').style.display = isExpanded ? '' : 'none';
        btn.title = isExpanded ? 'Collapse' : 'Expand';
        setCompactTable(container, isExpanded);
        setTimeout(() => { if (reportState.topDevicesChart) reportState.topDevicesChart.resize(); }, 450);
    });

    document.addEventListener('click', (e) => {
        if (!e.target.closest('.detail-filter-group')) closeAllDropdowns();
    });

    try {
        const hours = await (await fetch('/api/business-hours')).json();
        if (hours.success) reportState.hours = hours.hours;
    } catch (e) { /* keep the defaults */ }

    // Automatic Zabbix login
    try {
        const res = await fetch('/alarm/api/auto-login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        const data = await res.json();
        if (data.success) {
            reportState.sessionId = data.sessionId;
            reportState.zabbixUrl = data.url;
            const urlEl = document.getElementById('reportConnectedUrl');
            if (urlEl) urlEl.textContent = data.url;
            loadHostGroups();
            loadCacheStats();
        } else {
            showToast('Could not connect to Zabbix: ' + data.error, 'error');
        }
    } catch (e) {
        showToast('Zabbix connection error: ' + e.message, 'error');
    }
});

function handleReportLogout() {
    fetch(`${API_BASE}/logout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionId: reportState.sessionId })
    }).catch(() => { });
    window.location.href = '/';
}

function initDatePickers() {
    const now = new Date();
    const firstDay = new Date(now.getFullYear(), now.getMonth(), 1);

    flatpickr('#reportStartDate', {
        enableTime: true,
        time_24hr: true,
        dateFormat: 'Y-m-d H:i',
        defaultDate: firstDay,
        locale: { firstDayOfWeek: 1 }
    });

    flatpickr('#reportEndDate', {
        enableTime: true,
        time_24hr: true,
        dateFormat: 'Y-m-d H:i',
        defaultDate: now,
        locale: { firstDayOfWeek: 1 }
    });
}

// Convert a date string to a Unix timestamp in local time (timezone-safe)
function localDateToUnix(dateStr) {
    // flatpickr format: "2026-01-01 00:00"
    const parts = dateStr.split(/[- :]/);
    // new Date(year, monthIndex, day, hour, minute) -> ALWAYS local time
    const d = new Date(parseInt(parts[0]), parseInt(parts[1])-1, parseInt(parts[2]),
                       parseInt(parts[3]||0), parseInt(parts[4]||0), 0);
    return Math.floor(d.getTime() / 1000);
}

function initSeverityFilter() {
    const checkboxes = document.querySelectorAll('#reportSeverityFilter input[type="checkbox"]');
    checkboxes.forEach(cb => {
        cb.checked = reportState.selectedSeverities.has(cb.value);
        cb.addEventListener('change', () => {
            if (cb.checked) reportState.selectedSeverities.add(cb.value);
            else reportState.selectedSeverities.delete(cb.value);
        });
    });
}

// ===== EXCLUDED POPUP =====
function initExcludedPopup() {
    document.getElementById('showExcludedBtn').addEventListener('click', () => {
        renderExcludedList();
        document.getElementById('excludedPopup').classList.remove('hidden');
    });

    document.getElementById('closeExcludedPopup').addEventListener('click', () => {
        document.getElementById('excludedPopup').classList.add('hidden');
    });

    document.getElementById('closeExcludedPopupBottom').addEventListener('click', () => {
        document.getElementById('excludedPopup').classList.add('hidden');
    });

    document.getElementById('reAddAllBtn').addEventListener('click', () => {
        reportState.excludedCombinations.clear();
        updateExcludedUI();
        renderHostProblemCombinations();
        document.getElementById('excludedPopup').classList.add('hidden');
        showToast('All combinations added back', 'success');
    });

    // Close on overlay click
    document.getElementById('excludedPopup').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) {
            e.currentTarget.classList.add('hidden');
        }
    });
}

function renderExcludedList() {
    const container = document.getElementById('excludedList');
    const excluded = Array.from(reportState.excludedCombinations);

    if (excluded.length === 0) {
        container.innerHTML = '<div class="popup-empty">No excluded combinations.</div>';
        return;
    }

    container.innerHTML = excluded.map((key, i) => {
        const [host, problem] = key.split('|||');
        return `
            <div class="excluded-item">
                <div class="excluded-item-info">
                    <div class="excluded-item-host">${escapeHtml(host)}</div>
                    <div class="excluded-item-problem">${escapeHtml(problem)}</div>
                </div>
                <button class="btn-readd" data-index="${i}">+ Geri Ekle</button>
            </div>
        `;
    }).join('');

    container.querySelectorAll('.btn-readd').forEach(btn => {
        btn.addEventListener('click', () => {
            const idx = parseInt(btn.dataset.index);
            const key = excluded[idx];
            reportState.excludedCombinations.delete(key);
            updateExcludedUI();
            renderHostProblemCombinations();
            renderExcludedList();
            showToast('Combination added back', 'success');
        });
    });
}

function updateExcludedUI() {
    const count = reportState.excludedCombinations.size;
    const btn = document.getElementById('showExcludedBtn');
    const countEl = document.getElementById('excludedCount');

    if (count > 0) {
        btn.style.display = '';
        countEl.textContent = count;
    } else {
        btn.style.display = 'none';
    }
}

// ===== DETAIL TABLE FILTERS =====
function initDetailFilters() {
    // Severity filter button
    document.getElementById('filterSeverityBtn').addEventListener('click', (e) => {
        e.stopPropagation();
        toggleDropdown('severity');
    });

    // Host filter button
    document.getElementById('filterHostBtn').addEventListener('click', (e) => {
        e.stopPropagation();
        toggleDropdown('host');
    });

    // Mesai filter button
    document.getElementById('filterMesaiBtn').addEventListener('click', (e) => {
        e.stopPropagation();
        toggleDropdown('mesai');
    });

    // Category filter button
    document.getElementById('filterCategoryBtn').addEventListener('click', (e) => {
        e.stopPropagation();
        toggleDropdown('category');
    });

    // Problem text search
    document.getElementById('problemSearchInput').addEventListener('input', (e) => {
        reportState.problemSearchText = e.target.value.toLowerCase();
        applyDetailFilters();
    });

    // Host search within dropdown
    document.getElementById('filterHostSearch').addEventListener('input', (e) => {
        e.stopPropagation();
        renderHostDropdownItems(e.target.value.toLowerCase());
    });

    // Prevent dropdown close on click inside
    document.querySelectorAll('.detail-filter-dropdown').forEach(dd => {
        dd.addEventListener('click', (e) => e.stopPropagation());
    });

    // Clear all filters
    document.getElementById('clearAllFilters').addEventListener('click', () => {
        reportState.detailFilters.severity.clear();
        reportState.detailFilters.host.clear();
        reportState.detailFilters.mesai.clear();
        reportState.detailFilters.category.clear();
        reportState.problemSearchText = '';
        document.getElementById('problemSearchInput').value = '';
        applyDetailFilters();
        closeAllDropdowns();
    });
}

function toggleDropdown(type) {
    const dropdownId = `filter${capitalize(type)}Dropdown`;
    const btnId = `filter${capitalize(type)}Btn`;
    const dropdown = document.getElementById(dropdownId);
    const btn = document.getElementById(btnId);

    if (reportState.openDropdown === type) {
        closeAllDropdowns();
        return;
    }

    closeAllDropdowns();
    dropdown.classList.remove('hidden');
    btn.classList.add('active');
    reportState.openDropdown = type;
}

function closeAllDropdowns() {
    document.querySelectorAll('.detail-filter-dropdown').forEach(dd => dd.classList.add('hidden'));
    document.querySelectorAll('.detail-filter-btn').forEach(btn => btn.classList.remove('active'));
    reportState.openDropdown = null;
}

function capitalize(str) {
    return str.charAt(0).toUpperCase() + str.slice(1);
}

function populateDetailFilterDropdowns() {
    const data = reportState.filteredData;

    // Severity options
    const sevCounts = {};
    data.forEach(d => {
        const sev = d.severity || '0';
        sevCounts[sev] = (sevCounts[sev] || 0) + 1;
    });

    const sevContainer = document.getElementById('filterSeverityItems');
    sevContainer.innerHTML = ['5', '4', '3', '2', '1', '0']
        .filter(s => sevCounts[s])
        .map(s => {
            const info = SEVERITY_MAP[s];
            const checked = reportState.detailFilters.severity.has(s) ? 'checked' : '';
            const selectedClass = checked ? 'selected' : '';
            return `
                <div class="dropdown-item ${selectedClass}" data-value="${s}">
                    <input type="checkbox" ${checked}>
                    <span style="color:${info.color}">${info.name}</span>
                    <span class="item-count">${sevCounts[s]}</span>
                </div>
            `;
        }).join('');

    sevContainer.querySelectorAll('.dropdown-item').forEach(item => {
        item.addEventListener('click', () => {
            const val = item.dataset.value;
            const cb = item.querySelector('input[type="checkbox"]');
            if (reportState.detailFilters.severity.has(val)) {
                reportState.detailFilters.severity.delete(val);
                cb.checked = false;
                item.classList.remove('selected');
            } else {
                reportState.detailFilters.severity.add(val);
                cb.checked = true;
                item.classList.add('selected');
            }
            applyDetailFilters();
        });
    });

    // Host options
    renderHostDropdownItems('');

    // Mesai options
    const mesaiCounts = {};
    data.forEach(d => {
        mesaiCounts[d.mesai] = (mesaiCounts[d.mesai] || 0) + 1;
    });

    const mesaiContainer = document.getElementById('filterMesaiItems');
    mesaiContainer.innerHTML = Object.entries(mesaiCounts).map(([mesai, count]) => {
        const checked = reportState.detailFilters.mesai.has(mesai) ? 'checked' : '';
        const selectedClass = checked ? 'selected' : '';
        return `
            <div class="dropdown-item ${selectedClass}" data-value="${mesai}">
                <input type="checkbox" ${checked}>
                <span>${mesaiLabel(mesai)}</span>
                <span class="item-count">${count}</span>
            </div>
        `;
    }).join('');

    mesaiContainer.querySelectorAll('.dropdown-item').forEach(item => {
        item.addEventListener('click', () => {
            const val = item.dataset.value;
            const cb = item.querySelector('input[type="checkbox"]');
            if (reportState.detailFilters.mesai.has(val)) {
                reportState.detailFilters.mesai.delete(val);
                cb.checked = false;
                item.classList.remove('selected');
            } else {
                reportState.detailFilters.mesai.add(val);
                cb.checked = true;
                item.classList.add('selected');
            }
            applyDetailFilters();
        });
    });

    // Category options
    const catCounts = {};
    data.forEach(d => {
        const cat = categorizeProblem(d.name);
        catCounts[cat] = (catCounts[cat] || 0) + 1;
    });

    const catContainer = document.getElementById('filterCategoryItems');
    catContainer.innerHTML = Object.keys(PROBLEM_CATEGORIES)
        .filter(cat => catCounts[cat])
        .map(cat => {
            const info = PROBLEM_CATEGORIES[cat];
            const checked = reportState.detailFilters.category.has(cat) ? 'checked' : '';
            const selectedClass = checked ? 'selected' : '';
            return `
                <div class="dropdown-item ${selectedClass}" data-value="${cat}">
                    <input type="checkbox" ${checked}>
                    <span style="display:inline-flex;align-items:center;gap:6px;"><span style="width:8px;height:8px;border-radius:50%;background:${info.color};display:inline-block;"></span>${cat}</span>
                    <span class="item-count">${catCounts[cat]}</span>
                </div>
            `;
        }).join('');

    catContainer.querySelectorAll('.dropdown-item').forEach(item => {
        item.addEventListener('click', () => {
            const val = item.dataset.value;
            const cb = item.querySelector('input[type="checkbox"]');
            if (reportState.detailFilters.category.has(val)) {
                reportState.detailFilters.category.delete(val);
                cb.checked = false;
                item.classList.remove('selected');
            } else {
                reportState.detailFilters.category.add(val);
                cb.checked = true;
                item.classList.add('selected');
            }
            applyDetailFilters();
        });
    });
}

function renderHostDropdownItems(search) {
    const data = reportState.filteredData;
    const hostCounts = {};
    data.forEach(d => {
        hostCounts[d.host] = (hostCounts[d.host] || 0) + 1;
    });

    const sorted = Object.entries(hostCounts)
        .filter(([host]) => !search || host.toLowerCase().includes(search))
        .sort((a, b) => b[1] - a[1]);

    const container = document.getElementById('filterHostItems');
    container.innerHTML = sorted.slice(0, 50).map(([host, count]) => {
        const checked = reportState.detailFilters.host.has(host) ? 'checked' : '';
        const selectedClass = checked ? 'selected' : '';
        return `
            <div class="dropdown-item ${selectedClass}" data-value="${escapeHtml(host)}">
                <input type="checkbox" ${checked}>
                <span>${escapeHtml(host)}</span>
                <span class="item-count">${count}</span>
            </div>
        `;
    }).join('');

    container.querySelectorAll('.dropdown-item').forEach(item => {
        item.addEventListener('click', () => {
            const val = item.dataset.value;
            const cb = item.querySelector('input[type="checkbox"]');
            if (reportState.detailFilters.host.has(val)) {
                reportState.detailFilters.host.delete(val);
                cb.checked = false;
                item.classList.remove('selected');
            } else {
                reportState.detailFilters.host.add(val);
                cb.checked = true;
                item.classList.add('selected');
            }
            applyDetailFilters();
        });
    });
}

function applyDetailFilters() {
    const f = reportState.detailFilters;
    let data = reportState.filteredData;

    if (f.severity.size > 0) {
        data = data.filter(d => f.severity.has(d.severity));
    }
    if (f.host.size > 0) {
        data = data.filter(d => f.host.has(d.host));
    }
    if (f.mesai.size > 0) {
        data = data.filter(d => f.mesai.has(d.mesai));
    }
    if (f.category.size > 0) {
        data = data.filter(d => f.category.has(categorizeProblem(d.name)));
    }
    if (reportState.problemSearchText) {
        const search = reportState.problemSearchText;
        data = data.filter(d => d.name.toLowerCase().includes(search));
    }

    renderDetailTableRows(data);
    renderActiveFilters();
    updateFilterCounts();
}

function renderActiveFilters() {
    const f = reportState.detailFilters;
    const container = document.getElementById('activeFilters');
    const clearBtn = document.getElementById('clearAllFilters');
    let tags = [];

    f.severity.forEach(val => {
        const info = SEVERITY_MAP[val];
        tags.push(`<span class="filter-tag">${t('Severity')}: ${info.name}<button class="tag-remove" data-type="severity" data-value="${val}">&times;</button></span>`);
    });

    f.host.forEach(val => {
        tags.push(`<span class="filter-tag">Host: ${escapeHtml(val)}<button class="tag-remove" data-type="host" data-value="${escapeHtml(val)}">&times;</button></span>`);
    });

    f.mesai.forEach(val => {
        tags.push(`<span class="filter-tag">${mesaiLabel(val)}<button class="tag-remove" data-type="mesai" data-value="${val}">&times;</button></span>`);
    });

    f.category.forEach(val => {
        const catInfo = PROBLEM_CATEGORIES[val];
        tags.push(`<span class="filter-tag" style="border-left: 3px solid ${catInfo ? catInfo.color : '#888'};">${t('Category')}: ${t(val)}<button class="tag-remove" data-type="category" data-value="${val}">&times;</button></span>`);
    });

    if (reportState.problemSearchText) {
        tags.push(`<span class="filter-tag">${t('Search')}: "${escapeHtml(reportState.problemSearchText)}"<button class="tag-remove" data-type="problemSearch" data-value="clear">&times;</button></span>`);
    }

    container.innerHTML = tags.join('');

    if (tags.length > 0) {
        clearBtn.classList.remove('hidden');
    } else {
        clearBtn.classList.add('hidden');
    }

    container.querySelectorAll('.tag-remove').forEach(btn => {
        btn.addEventListener('click', () => {
            const type = btn.dataset.type;
            const value = btn.dataset.value;
            if (type === 'problemSearch') {
                reportState.problemSearchText = '';
                document.getElementById('problemSearchInput').value = '';
            } else {
                reportState.detailFilters[type].delete(value);
            }
            applyDetailFilters();
            // Also refresh dropdown if open
            if (reportState.openDropdown === type) {
                populateDetailFilterDropdowns();
            }
        });
    });
}

function updateFilterCounts() {
    const f = reportState.detailFilters;
    document.getElementById('filterSeverityCount').textContent = f.severity.size > 0 ? f.severity.size : '';
    document.getElementById('filterHostCount').textContent = f.host.size > 0 ? f.host.size : '';
    document.getElementById('filterMesaiCount').textContent = f.mesai.size > 0 ? f.mesai.size : '';
    document.getElementById('filterCategoryCount').textContent = f.category.size > 0 ? f.category.size : '';
}

// ===== HOST GROUPS =====
async function loadHostGroups() {
    try {
        const result = await apiRequest('/hostgroups', {});
        reportState.hostGroups = result;
        renderHostGroupChips();
    } catch (error) {
        showToast('Could not load host groups: ' + error.message, 'error');
    }
}

function getFilteredReportGroups() {
    return reportState.hostGroups.filter(g =>
        !reportState.hostSearchFilter || g.name.toLowerCase().includes(reportState.hostSearchFilter)
    );
}

function renderHostGroupChips() {
    const container = document.getElementById('reportHostGroups');
    const filtered = getFilteredReportGroups();

    if (filtered.length === 0) {
        container.innerHTML = '<div class="empty-state">No results</div>';
    } else {
        container.innerHTML = filtered.map((g, i) => `
            <div class="report-host-chip ${reportState.selectedGroups.has(g.groupid) ? 'selected' : ''}"
                 data-id="${g.groupid}" data-index="${i}"
                 onclick="toggleReportGroup('${g.groupid}', ${i}, event)">
                ${escapeHtml(g.name)} (${g.hosts})
            </div>
        `).join('');
    }

    const countEl = document.getElementById('selectedGroupCount');
    if (countEl) countEl.textContent = t('{0} selected', reportState.selectedGroups.size);

    // Update the button texts
    const filtered2 = getFilteredReportGroups();
    const visBtn = document.getElementById('rptSelectVisibleBtn');
    if (visBtn) {
        const allVisSel = filtered2.length > 0 && filtered2.every(g => reportState.selectedGroups.has(g.groupid));
        visBtn.textContent = allVisSel ? 'Deselect Visible' : 'Select Visible';
    }
    const allBtn = document.getElementById('rptSelectAllBtn');
    if (allBtn) {
        const allSel = reportState.hostGroups.length > 0 && reportState.selectedGroups.size === reportState.hostGroups.length;
        allBtn.textContent = allSel ? 'Deselect All' : 'Select All';
    }
}

function rptSelectAll() {
    const allSel = reportState.selectedGroups.size === reportState.hostGroups.length;
    reportState.selectedGroups.clear();
    if (!allSel) reportState.hostGroups.forEach(g => reportState.selectedGroups.add(g.groupid));
    reportState.lastChipIndex = -1;
    renderHostGroupChips();
}

function rptSelectVisible() {
    const visible = getFilteredReportGroups();
    const allVisSel = visible.length > 0 && visible.every(g => reportState.selectedGroups.has(g.groupid));
    if (allVisSel) {
        visible.forEach(g => reportState.selectedGroups.delete(g.groupid));
    } else {
        visible.forEach(g => reportState.selectedGroups.add(g.groupid));
    }
    reportState.lastChipIndex = -1;
    renderHostGroupChips();
}

function rptClearSel() {
    reportState.selectedGroups.clear();
    reportState.lastChipIndex = -1;
    renderHostGroupChips();
}

function toggleReportGroup(groupId, index, event) {
    const filtered = getFilteredReportGroups();
    if (event && event.shiftKey && reportState.lastChipIndex !== -1 && reportState.lastChipIndex !== index) {
        // Shift: select all groups in the range
        const start = Math.min(reportState.lastChipIndex, index);
        const end   = Math.max(reportState.lastChipIndex, index);
        for (let i = start; i <= end; i++) {
            reportState.selectedGroups.add(filtered[i].groupid);
        }
    } else {
        if (reportState.selectedGroups.has(groupId)) {
            reportState.selectedGroups.delete(groupId);
        } else {
            reportState.selectedGroups.add(groupId);
        }
    }
    reportState.lastChipIndex = index;
    renderHostGroupChips();
}

// ===== GENERATE REPORT =====
async function generateReport() {
    const startDate = document.getElementById('reportStartDate').value;
    const endDate = document.getElementById('reportEndDate').value;

    if (!startDate || !endDate) {
        showToast('Please select a date range', 'warning');
        return;
    }

    if (reportState.selectedSeverities.size === 0) {
        showToast('Select at least one severity', 'warning');
        return;
    }

    showLoading('Generating report...');

    // Show the progress bar
    const progressContainer = document.getElementById('progressContainer');
    const progressBarFill = document.getElementById('progressBarFill');
    const progressText = document.getElementById('progressText');
    const progressDetail = document.getElementById('progressDetail');
    progressContainer.style.display = '';
    progressBarFill.style.width = '0%';
    progressText.textContent = pctText(0);
    progressDetail.textContent = 'Starting...';

    // Add a cancel button
    let cancelBtn = document.getElementById('reportCancelBtn');
    if (!cancelBtn) {
        cancelBtn = document.createElement('button');
        cancelBtn.id = 'reportCancelBtn';
        cancelBtn.className = 'btn btn-cancel-report';
        cancelBtn.innerHTML = '✕ Cancel Report';
        cancelBtn.style.cssText = 'margin-top:12px;padding:8px 24px;border-radius:8px;border:1px solid #ef4444;background:rgba(239,68,68,0.1);color:#ef4444;cursor:pointer;font-size:14px;font-weight:500;transition:all 0.2s;';
        cancelBtn.onmouseenter = () => { cancelBtn.style.background = 'rgba(239,68,68,0.25)'; };
        cancelBtn.onmouseleave = () => { cancelBtn.style.background = 'rgba(239,68,68,0.1)'; };
        progressContainer.appendChild(cancelBtn);
    }
    cancelBtn.style.display = '';
    cancelBtn.disabled = false;
    cancelBtn.innerHTML = '✕ Cancel Report';

    let currentJobId = null;
    let progressInterval = null;
    let isCancelled = false;

    cancelBtn.onclick = async () => {
        if (currentJobId && !isCancelled) {
            isCancelled = true;
            cancelBtn.disabled = true;
            cancelBtn.innerHTML = 'Cancelling...';
            try {
                await fetch(`${API_BASE}/report-cancel`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ jobId: currentJobId })
                });
            } catch (e) { /* ignore */ }
        }
    };

    try {
        const startTimestamp = localDateToUnix(startDate);
        const endTimestamp = localDateToUnix(endDate);

        // STEP 1: start the report job (returns immediately)
        const startResponse = await fetch(`${API_BASE}/report-data`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                timeFrom: startTimestamp,
                timeTill: endTimestamp,
                groupIds: Array.from(reportState.selectedGroups),
                sessionId: reportState.sessionId
            })
        });

        const startResult = await startResponse.json();
        if (!startResult.success) throw new Error(startResult.error || 'Could not start the job');

        currentJobId = startResult.jobId;

        // STEP 2: track progress via polling
        const result = await new Promise((resolve, reject) => {
            progressInterval = setInterval(async () => {
                if (isCancelled) {
                    clearInterval(progressInterval);
                    reject(new Error('Report cancelled'));
                    return;
                }

                try {
                    const res = await fetch(`${API_BASE}/report-progress`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ jobId: currentJobId })
                    });
                    const pData = await res.json();

                    if (pData.success && pData.data) {
                        const p = pData.data;
                        const pct = Math.min(p.percent, 99);
                        progressBarFill.style.width = `${pct}%`;
                        progressText.textContent = pctText(pct);
                        progressDetail.textContent = t('{0} — {1} alarms found', I18N.msg(p.step), p.events);
                        
                        const loadingText = document.getElementById('loadingText');
                        if (loadingText) {
                            loadingText.textContent = `${I18N.msg(p.step)} — ${pctText(pct)}`;
                        }
                    }

                    // Check whether the job has completed
                    if (pData.status === 'done') {
                        clearInterval(progressInterval);

                        // STEP 3: fetch the result
                        progressBarFill.style.width = '100%';
                        progressText.textContent = pctText(100);
                        progressDetail.textContent = 'Fetching report data...';

                        const resultRes = await fetch(`${API_BASE}/report-result`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ jobId: currentJobId })
                        });
                        const resultJson = await resultRes.json();

                        if (!resultJson.success) {
                            reject(new Error(resultJson.error || 'Could not fetch the result'));
                        } else {
                            resolve(resultJson.data);
                        }
                    } else if (pData.status === 'error') {
                        clearInterval(progressInterval);
                        reject(new Error(pData.error || 'An error occurred while generating the report'));
                    } else if (pData.status === 'cancelled') {
                        clearInterval(progressInterval);
                        reject(new Error('Report cancelled'));
                    }
                } catch (e) {
                    // Swallow the polling error but check for a dropped connection
                    if (e.message === 'Report cancelled' || e.message?.includes('Could not fetch the result')) {
                        clearInterval(progressInterval);
                        reject(e);
                    }
                    // Ignore other polling errors, retry
                }
            }, 1000); // 500ms → 1000ms (the backend job is threaded, so polling less often is enough)
        });

        // === REPORT DATA RECEIVED ===
        // Normalize the shift value to a canonical form: old cache had "Mesai Ici/Disi" (ASCII),
        // new data may come as "Mesai İçi/Dışı". Comparisons use "Mesai İçi", so without
        // normalizing, everything was counted as "Mesai Dışı".
        result.forEach(d => { d.mesai = normalizeMesai(d.mesai); });
        reportState.reportData = result;
        reportState.range = { from: startTimestamp, till: endTimestamp };
        resetComparison();
        reportState.excludedCombinations.clear();
        reportState.excludedDevices.clear();
        reportState.detailFilters.severity.clear();
        reportState.detailFilters.host.clear();
        reportState.detailFilters.mesai.clear();
        reportState.detailFilters.category.clear();
        reportState.problemSearchText = '';

        // Filter by severity
        reportState.filteredData = result.filter(d =>
            reportState.selectedSeverities.has(d.severity)
        );

        renderGeneralStats(startDate, endDate);
        renderSeverityDistribution();
        renderMesaiDistribution();
        renderHostProblemCombinations();
        renderTopDevices();

        // SLA Availability (optional)
        const includeSla = document.getElementById('includeSlaReport').checked;
        if (includeSla) {
            await renderAvailabilityReport(startTimestamp, endTimestamp);
        } else {
            document.getElementById('slaSection').style.display = 'none';
        }

        renderDetailTable();
        populateDetailFilterDropdowns();
        updateExcludedUI();
        updateExcludedDevicesUI();

        document.getElementById('reportContent').classList.remove('hidden');
        document.getElementById('exportReportBtn').style.display = '';
        document.getElementById('exportPdfBtn').style.display = '';

        // Collapse filter panel and scroll to report
        document.querySelector('.report-filters-panel').classList.add('collapsed');
        setTimeout(() => {
            document.getElementById('reportContent').scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 350);

        showToast(t('Report generated - {0} alarms', reportState.filteredData.length), 'info');
        loadCacheStats();
    } catch (error) {
        if (error.message !== 'Report cancelled') {
            showToast('Could not generate the report: ' + error.message, 'error');
        } else {
            showToast('Report cancelled', 'warning');
        }
    } finally {
        if (progressInterval) clearInterval(progressInterval);
        progressContainer.style.display = 'none';
        if (cancelBtn) cancelBtn.style.display = 'none';
        hideLoading();
    }
}

// ===== GENERAL STATS =====
function renderGeneralStats(startDate, endDate) {
    const data = reportState.filteredData;
    const total = data.length;
    const mesaiIci = data.filter(d => d.mesai === 'Mesai İçi').length;
    const mesaiDisi = total - mesaiIci;
    const mesaiIciPct = total > 0 ? ((mesaiIci / total) * 100).toFixed(1) : '0.0';
    const mesaiDisiPct = total > 0 ? ((mesaiDisi / total) * 100).toFixed(1) : '0.0';

    const ICO = {
        bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
        calStart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
        calEnd: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><polyline points="9 16 11 18 15 14"/></svg>',
        sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/></svg>',
        moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
    };
    const kpi = (cls, label, value, icoColor, ico, detail) => `
        <div class="stat-card ${cls}">
            <div class="stat-top">
                <span class="stat-label">${label}</span>
                <span class="stat-ico" style="background:${icoColor}1f;color:${icoColor}">${ico}</span>
            </div>
            <span class="stat-value">${value}</span>
            ${detail ? `<span class="stat-detail">${detail}</span>` : ''}
        </div>`;

    document.getElementById('generalStats').innerHTML =
        kpi('total', 'Total Alarms', total.toLocaleString(I18N.locale), '#f59e0b', ICO.bell, '') +
        kpi('date', 'Start Date', `<span class="stat-value-sm">${startDate}</span>`, '#a59f91', ICO.calStart, '') +
        kpi('date', 'End Date', `<span class="stat-value-sm">${endDate}</span>`, '#a59f91', ICO.calEnd, '') +
        kpi('mesai-ici', 'Business hours', mesaiIci.toLocaleString(I18N.locale), '#4CAF50', ICO.sun, `${pctText(mesaiIciPct)} · ${hoursRange()}`) +
        kpi('mesai-disi', 'After hours', mesaiDisi.toLocaleString(I18N.locale), '#f59e0b', ICO.moon, pctText(mesaiDisiPct));
}

// ===== SEVERITY DISTRIBUTION =====
function renderSeverityDistribution() {
    const data = reportState.filteredData;
    const total = data.length;

    const counts = {};
    const hostSets = {};
    for (const sev of ['5', '4', '3', '2', '1', '0']) {
        counts[sev] = 0;
        hostSets[sev] = new Set();
    }

    data.forEach(d => {
        const sev = d.severity || '0';
        counts[sev] = (counts[sev] || 0) + 1;
        if (d.host) hostSets[sev]?.add(d.host);
    });

    const tbody = document.querySelector('#severityTable tbody');
    tbody.innerHTML = '';
    for (const sev of ['5', '4', '3', '2', '1', '0']) {
        const info = SEVERITY_MAP[sev];
        const count = counts[sev] || 0;
        if (count === 0) continue;
        const pct = total > 0 ? ((count / total) * 100).toFixed(1) : '0.0';
        const hostCount = hostSets[sev] ? hostSets[sev].size : 0;

        const tr = document.createElement('tr');
        tr.className = info.rowClass;
        tr.innerHTML = `
            <td><span class="severity-badge-table ${info.class}">${info.name}</span></td>
            <td><strong>${count.toLocaleString(I18N.locale)}</strong></td>
            <td>${pct}%</td>
            <td>${hostCount}</td>
        `;
        tbody.appendChild(tr);
    }

    renderSeverityChart(counts);
}

function renderSeverityChart(counts) {
    const ctx = document.getElementById('severityChart').getContext('2d');
    if (reportState.severityChart) reportState.severityChart.destroy();

    const labels = [], values = [], colors = [];
    for (const sev of ['5', '4', '3', '2', '1', '0']) {
        if ((counts[sev] || 0) > 0) {
            labels.push(SEVERITY_MAP[sev].name);
            values.push(counts[sev]);
            colors.push(SEVERITY_MAP[sev].color);
        }
    }

    const total = values.reduce((a, b) => a + b, 0);

    reportState.severityChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels,
            datasets: [{ data: values, backgroundColor: colors, borderColor: '#1a1610', borderWidth: 3 }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'bottom', labels: { color: '#ccc', padding: 15, font: { size: 12 } } },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const pct = ((ctx.raw / total) * 100).toFixed(1);
                            return ` ${ctx.label}: ${ctx.raw} (${pctText(pct)})`;
                        }
                    }
                },
                datalabels: {
                    color: '#fff',
                    font: { weight: 'bold', size: 12 },
                    textShadowColor: 'rgba(0,0,0,0.6)',
                    textShadowBlur: 4,
                    formatter: (value) => {
                        const pct = ((value / total) * 100).toFixed(1);
                        return pctText(pct);
                    },
                    display: (ctx) => {
                        const pct = (ctx.dataset.data[ctx.dataIndex] / total) * 100;
                        return pct >= 3;
                    }
                }
            }
        }
    });
}

// ===== MESAI DISTRIBUTION =====
function renderMesaiDistribution() {
    const data = reportState.filteredData;
    const total = data.length;
    const mesaiIci = data.filter(d => d.mesai === 'Mesai İçi').length;
    const mesaiDisi = total - mesaiIci;

    document.getElementById('hoursNote').textContent = t('* Business hours: {0}, {1}', hoursDays(), hoursRange());
    document.querySelector('#mesaiTable tbody').innerHTML = `
        <tr>
            <td><strong>Business hours</strong></td>
            <td><strong>${mesaiIci.toLocaleString(I18N.locale)}</strong></td>
            <td>${total > 0 ? ((mesaiIci / total) * 100).toFixed(1) : '0.0'}%</td>
            <td>${hoursRange()} (${hoursDays()})</td>
        </tr>
        <tr>
            <td><strong>After hours</strong></td>
            <td><strong>${mesaiDisi.toLocaleString(I18N.locale)}</strong></td>
            <td>${total > 0 ? ((mesaiDisi / total) * 100).toFixed(1) : '0.0'}%</td>
            <td>${t('Outside business hours')}</td>
        </tr>
        <tr style="border-top: 2px solid var(--border-color);">
            <td><strong>TOTAL</strong></td>
            <td><strong>${total.toLocaleString(I18N.locale)}</strong></td>
            <td>100.0%</td>
            <td>All alarms</td>
        </tr>
    `;

    renderMesaiChart(mesaiIci, mesaiDisi);
}

function renderMesaiChart(mesaiIci, mesaiDisi) {
    const ctx = document.getElementById('mesaiChart').getContext('2d');
    if (reportState.mesaiChart) reportState.mesaiChart.destroy();

    const total = mesaiIci + mesaiDisi;
    if (total === 0) return;

    const labels = [t('Business hours'), t('After hours')];
    const values = [mesaiIci, mesaiDisi];
    const colors = ['#36d399', '#a78bfa'];

    reportState.mesaiChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels,
            datasets: [{ data: values, backgroundColor: colors, borderColor: '#1a1610', borderWidth: 3 }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'bottom', labels: { color: '#ccc', padding: 15, font: { size: 12 } } },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const pct = ((ctx.raw / total) * 100).toFixed(1);
                            return ` ${ctx.label}: ${ctx.raw.toLocaleString(I18N.locale)} (${pctText(pct)})`;
                        }
                    }
                },
                datalabels: {
                    color: '#fff',
                    font: { weight: 'bold', size: 14 },
                    textShadowColor: 'rgba(0,0,0,0.6)',
                    textShadowBlur: 4,
                    formatter: (value) => {
                        const pct = ((value / total) * 100).toFixed(1);
                        return pctText(pct);
                    },
                    display: (ctx) => {
                        const pct = (ctx.dataset.data[ctx.dataIndex] / total) * 100;
                        return pct >= 3;
                    }
                }
            }
        }
    });
}

// ===== HOST-PROBLEM COMBINATIONS =====
function renderHostProblemCombinations() {
    const data = reportState.filteredData;
    const combMap = {};
    data.forEach(d => {
        const key = `${d.host}|||${d.name}`;
        combMap[key] = (combMap[key] || 0) + 1;
    });

    let sorted = Object.entries(combMap)
        .filter(([key]) => !reportState.excludedCombinations.has(key))
        .sort((a, b) => b[1] - a[1]);

    const top10 = sorted.slice(0, 10);
    reportState.currentTop10 = top10;

    const tbody = document.querySelector('#hostProblemTable tbody');
    tbody.innerHTML = top10.map(([key, count], i) => {
        const [host, problem] = key.split('|||');
        return `
            <tr>
                <td>${i + 1}</td>
                <td><strong>${escapeHtml(host)}</strong></td>
                <td>${escapeHtml(problem)}</td>
                <td><strong>${count.toLocaleString(I18N.locale)}</strong></td>
                <td><button class="btn-exclude" data-index="${i}">✕</button></td>
            </tr>
        `;
    }).join('');

    tbody.querySelectorAll('.btn-exclude').forEach(btn => {
        btn.addEventListener('click', () => {
            const idx = parseInt(btn.dataset.index);
            const key = top10[idx][0];
            reportState.excludedCombinations.add(key);
            updateExcludedUI();
            renderHostProblemCombinations();
            showToast('Combination removed from the list', 'info');
        });
    });

    renderHostProblemChart(top10);
}

// Table compact/full transition timing.
// Growing: make the table compact IMMEDIATELY (so it fits the narrow column).
// Shrinking: STAY compact until the column-expand animation (0.4s) finishes,
// then switch to full. This keeps full content from being squeezed into a narrow column.
function setCompactTable(container, isExpanded) {
    if (container._compactTimer) {
        clearTimeout(container._compactTimer);
        container._compactTimer = null;
    }
    if (isExpanded) {
        container.classList.add('compact-table');
    } else {
        container._compactTimer = setTimeout(() => {
            container.classList.remove('compact-table');
            container._compactTimer = null;
        }, 420);
    }
}

function renderHostProblemChart(top10) {
    const ctx = document.getElementById('hostProblemChart').getContext('2d');
    if (reportState.hostProblemChart) reportState.hostProblemChart.destroy();

    const labels = top10.map(([key]) => {
        const host = key.split('|||')[0];
        return host.length > 20 ? host.substring(0, 20) + '...' : host;
    });
    const values = top10.map(([, count]) => count);

    const colors = [
        '#6366f1', '#8b5cf6', '#a855f7', '#d946ef', '#ec4899',
        '#f43f5e', '#f97316', '#eab308', '#22c55e', '#14b8a6'
    ];

    reportState.hostProblemChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: t('Occurrences'),
                data: values,
                backgroundColor: colors.slice(0, values.length),
                borderColor: colors.slice(0, values.length),
                borderWidth: 1,
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                legend: { display: false },
                datalabels: { display: false },
                tooltip: {
                    callbacks: {
                        title: (items) => {
                            const idx = items[0].dataIndex;
                            return top10[idx][0].split('|||')[0];
                        },
                        label: (ctx) => {
                            const idx = ctx.dataIndex;
                            const problem = top10[idx][0].split('|||')[1];
                            return [`Problem: ${problem}`, t('Occurrences: {0}', ctx.raw)];
                        }
                    }
                }
            },
            scales: {
                x: { ticks: { color: '#aaa' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                y: { ticks: { color: '#ccc', font: { size: 11 } }, grid: { display: false } }
            }
        }
    });
}

// ===== TOP 10 MOST ALARMED DEVICES =====
function renderTopDevices() {
    const data = reportState.filteredData;
    const hostMap = {};
    const hostProblemMap = {};
    data.forEach(d => {
        hostMap[d.host] = (hostMap[d.host] || 0) + 1;
        if (!hostProblemMap[d.host]) hostProblemMap[d.host] = {};
        hostProblemMap[d.host][d.name] = (hostProblemMap[d.host][d.name] || 0) + 1;
    });

    let sorted = Object.entries(hostMap)
        .filter(([host]) => !reportState.excludedDevices.has(host))
        .sort((a, b) => b[1] - a[1]);

    const top10 = sorted.slice(0, 10);
    reportState.currentTopDevices = top10;

    const tbody = document.querySelector('#topDevicesTable tbody');
    tbody.innerHTML = top10.map(([host, count], i) => {
        // Get top 3 problems for this host
        const problems = hostProblemMap[host] || {};
        const topProblems = Object.entries(problems)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 3);
        const tooltipRows = topProblems.map(([p, c]) =>
            `<div class="host-tooltip-row"><span class="host-tooltip-problem">${escapeHtml(p)}</span><span class="host-tooltip-count">${c.toLocaleString(I18N.locale)}</span></div>`
        ).join('');

        return `
            <tr>
                <td>${i + 1}</td>
                <td>
                    <div class="host-cell-hover">
                        <strong>${escapeHtml(host)}</strong>
                        <div class="host-tooltip">
                            <div class="host-tooltip-title">Most Frequent Problems</div>
                            ${tooltipRows}
                        </div>
                    </div>
                </td>
                <td><strong>${count.toLocaleString(I18N.locale)}</strong></td>
                <td><button class="btn-exclude" data-index="${i}">✕</button></td>
            </tr>
        `;
    }).join('');

    tbody.querySelectorAll('.btn-exclude').forEach(btn => {
        btn.addEventListener('click', () => {
            const idx = parseInt(btn.dataset.index);
            const host = top10[idx][0];
            reportState.excludedDevices.add(host);
            updateExcludedDevicesUI();
            renderTopDevices();
            showToast('Device removed from the list', 'info');
        });
    });

    renderTopDevicesChart(top10);
}

function renderTopDevicesChart(top10) {
    const ctx = document.getElementById('topDevicesChart').getContext('2d');
    if (reportState.topDevicesChart) reportState.topDevicesChart.destroy();

    const labels = top10.map(([host]) => {
        return host.length > 20 ? host.substring(0, 20) + '...' : host;
    });
    const values = top10.map(([, count]) => count);

    const colors = [
        '#14b8a6', '#22c55e', '#eab308', '#f97316', '#f43f5e',
        '#ec4899', '#d946ef', '#a855f7', '#8b5cf6', '#6366f1'
    ];

    reportState.topDevicesChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: t('Alarm Count'),
                data: values,
                backgroundColor: colors.slice(0, values.length),
                borderColor: colors.slice(0, values.length),
                borderWidth: 1,
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                legend: { display: false },
                datalabels: {
                    anchor: 'end',
                    align: 'right',
                    color: '#ccc',
                    font: { size: 11, weight: '600' },
                    formatter: (value) => value
                },
                tooltip: {
                    callbacks: {
                        title: (items) => {
                            const idx = items[0].dataIndex;
                            return top10[idx][0];
                        },
                        label: (ctx) => {
                            return t('Alarm Count: {0}', ctx.raw);
                        }
                    }
                }
            },
            layout: {
                padding: { right: 40 }
            },
            scales: {
                x: { ticks: { color: '#aaa' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                y: { ticks: { color: '#ccc', font: { size: 11 } }, grid: { display: false } }
            }
        }
    });
}

// ===== SLA AVAILABILITY =====
let _slaDataCache = [];

async function renderAvailabilityReport(timeFrom, timeTill) {
    const section = document.getElementById('slaSection');
    const emptyState = document.getElementById('slaEmptyState');

    try {
        const slaData = await apiRequest('/sla-data', {
            timeFrom: timeFrom,
            timeTill: timeTill
        });

        if (!slaData || slaData.length === 0) {
            section.style.display = 'none';
            _slaDataCache = [];
            return;
        }

        _slaDataCache = slaData.filter(item => item.sli < 100);

        if (_slaDataCache.length === 0) {
            section.style.display = 'none';
            return;
        }

        section.style.display = '';
        emptyState.classList.add('hidden');

        renderSlaTable();

        const hideZeroBtn = document.getElementById('hideZeroSli');
        hideZeroBtn.replaceWith(hideZeroBtn.cloneNode(true));
        document.getElementById('hideZeroSli').addEventListener('click', function () {
            this.classList.toggle('active');
            renderSlaTable();
        });

    } catch (err) {
        console.warn('Could not fetch SLA data:', err);
        section.style.display = 'none';
    }
}

function renderSlaTable() {
    const tbody = document.querySelector('#slaTable tbody');
    const countBadge = document.getElementById('slaCount');
    const hideZero = document.getElementById('hideZeroSli').classList.contains('active');

    let data = [..._slaDataCache];
    if (hideZero) {
        data = data.filter(item => item.sli > 0);
    }

    data.sort((a, b) => b.sli - a.sli);

    countBadge.textContent = data.length;

    if (data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:2rem;">No SLA data to show</td></tr>`;
        return;
    }

    tbody.innerHTML = data.map(item => {
        const sli = item.sli;
        const slo = item.slo;
        const isOk = item.status === 'ok';
        const isError = item.status === 'error';

        const statusClass = isError ? 'sla-error' : (isOk ? 'sla-ok' : 'sla-violation');
        const statusText = isError ? '⚠ Error' : (isOk ? '✓ OK' : '✗ Violation');

        const barColor = isOk ? '#22c55e' : '#ef4444';
        const barWidth = Math.min(sli, 100);

        return `
            <tr class="${statusClass}">
                <td>${escapeHtml(item.slaName)}</td>
                <td>${slo.toFixed(2)}%</td>
                <td>
                    <div class="sla-sli-cell">
                        <span class="sla-sli-value">${sli.toFixed(4)}%</span>
                        <div class="sla-progress-bar">
                            <div class="sla-progress-fill" style="width:${barWidth}%;background:${barColor}"></div>
                            <div class="sla-slo-marker" style="left:${Math.min(slo, 100)}%"></div>
                        </div>
                    </div>
                </td>
                <td>${formatDurationLong(item.uptime)}</td>
                <td>${formatDurationLong(item.downtime)}</td>
                <td><span class="sla-status-badge ${statusClass}">${statusText}</span></td>
            </tr>
        `;
    }).join('');
}

function formatDurationLong(seconds) {
    if (!seconds || seconds <= 0) return `0${durUnits().s}`;
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    const u = durUnits();
    let parts = [];
    if (d > 0) parts.push(`${d}${u.d}`);
    if (h > 0) parts.push(`${h}${u.h}`);
    if (m > 0) parts.push(`${m}${u.m}`);
    if (s > 0 && d === 0) parts.push(`${s}${u.s}`);
    return parts.join(' ') || `0${u.s}`;
}

function initExcludedDevicesPopup() {
    document.getElementById('showExcludedDevicesBtn').addEventListener('click', () => {
        renderExcludedDevicesList();
        document.getElementById('excludedDevicesPopup').classList.remove('hidden');
    });

    document.getElementById('closeExcludedDevicesPopup').addEventListener('click', () => {
        document.getElementById('excludedDevicesPopup').classList.add('hidden');
    });

    document.getElementById('closeExcludedDevicesPopupBottom').addEventListener('click', () => {
        document.getElementById('excludedDevicesPopup').classList.add('hidden');
    });

    document.getElementById('reAddAllDevicesBtn').addEventListener('click', () => {
        reportState.excludedDevices.clear();
        updateExcludedDevicesUI();
        renderTopDevices();
        document.getElementById('excludedDevicesPopup').classList.add('hidden');
        showToast('All devices added back', 'success');
    });

    document.getElementById('excludedDevicesPopup').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) {
            e.currentTarget.classList.add('hidden');
        }
    });
}

function renderExcludedDevicesList() {
    const container = document.getElementById('excludedDevicesList');
    const excluded = Array.from(reportState.excludedDevices);

    if (excluded.length === 0) {
        container.innerHTML = '<div class="popup-empty">No excluded devices.</div>';
        return;
    }

    container.innerHTML = excluded.map((host, i) => {
        return `
            <div class="excluded-item">
                <div class="excluded-item-info">
                    <div class="excluded-item-host">${escapeHtml(host)}</div>
                </div>
                <button class="btn-readd" data-index="${i}">+ Geri Ekle</button>
            </div>
        `;
    }).join('');

    container.querySelectorAll('.btn-readd').forEach(btn => {
        btn.addEventListener('click', () => {
            const idx = parseInt(btn.dataset.index);
            const host = excluded[idx];
            reportState.excludedDevices.delete(host);
            updateExcludedDevicesUI();
            renderTopDevices();
            renderExcludedDevicesList();
            showToast('Device added back', 'success');
        });
    });
}

function updateExcludedDevicesUI() {
    const count = reportState.excludedDevices.size;
    const btn = document.getElementById('showExcludedDevicesBtn');
    const countEl = document.getElementById('excludedDevicesCount');

    if (count > 0) {
        btn.style.display = '';
        countEl.textContent = count;
    } else {
        btn.style.display = 'none';
    }
}

// Normalizes the shift label to a single canonical form.
// Accepted: "Mesai İçi"/"Mesai Dışı" (new) and "Mesai Ici"/"Mesai Disi" (old cache).
// Of the four values, only the "Dışı/Disi" variants contain a 'd'; we split on that.
// (Turkish locale lowercases 'I'->'ı', so searching for 'ici' is unreliable; hence the 'd' check.)
function normalizeMesai(v) {
    const s = (v == null ? '' : String(v)).toLowerCase();
    return s.indexOf('d') !== -1 ? 'Mesai Dışı' : 'Mesai İçi';
}

// ===== DETAIL TABLE =====
function renderDetailTable() {
    renderDetailTableRows(reportState.filteredData);
}

function renderDetailTableRows(data) {
    document.getElementById('detailAlarmCount').textContent = data.length;

    document.querySelector('#detailTable tbody').innerHTML = data.map(d => {
        const sev = SEVERITY_MAP[d.severity] || SEVERITY_MAP['0'];
        const date = formatDate(d.clock);
        const status = d.resolved ? 'Resolved' : 'Problem';
        const statusClass = d.resolved ? 'status-resolved' : 'status-problem';
        const duration = d.resolved ? formatDuration(d.duration) : '-';
        const mesaiClass = d.mesai === 'Mesai İçi' ? 'ici' : 'disi';

        return `
            <tr>
                <td><span class="severity-badge-table ${sev.class}">${sev.name}</span></td>
                <td>${date}</td>
                <td><strong>${escapeHtml(d.host)}</strong></td>
                <td>${escapeHtml(d.name)}</td>
                <td><span class="${statusClass}">${status}</span></td>
                <td>${duration}</td>
                <td><span class="mesai-badge ${mesaiClass}">${mesaiLabel(d.mesai)}</span></td>
            </tr>
        `;
    }).join('');
}

// ===== EXPORT =====
async function exportReport() {
    showToast('Preparing the Excel report...', 'info');

    try {
        const workbook = new ExcelJS.Workbook();
        workbook.creator = 'Zabbix Report';
        workbook.created = new Date();

        const data = reportState.filteredData;
        const startDate = document.getElementById('reportStartDate').value;
        const endDate = document.getElementById('reportEndDate').value;

        const headerFill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF1E1B4B' } };
        const headerFont = { bold: true, color: { argb: 'FFFFFFFF' }, size: 11, name: 'Calibri' };
        const headerAlignment = { vertical: 'middle', horizontal: 'center', wrapText: true };
        const sectionTitleFill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF312E81' } };
        const sectionTitleFont = { bold: true, color: { argb: 'FFFFFFFF' }, size: 13, name: 'Calibri' };
        const darkBorder = {
            top: { style: 'thin', color: { argb: 'FF4338CA' } },
            bottom: { style: 'thin', color: { argb: 'FF4338CA' } },
            left: { style: 'thin', color: { argb: 'FF4338CA' } },
            right: { style: 'thin', color: { argb: 'FF4338CA' } }
        };
        const cellBorder = {
            top: { style: 'thin', color: { argb: 'FF9CA3AF' } },
            bottom: { style: 'thin', color: { argb: 'FF9CA3AF' } },
            left: { style: 'thin', color: { argb: 'FF9CA3AF' } },
            right: { style: 'thin', color: { argb: 'FF9CA3AF' } }
        };
        const rowFillEven = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFF3F4F6' } };
        const rowFillOdd = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFE5E7EB' } };

        const ws = workbook.addWorksheet(t('Report'));
        ws.getColumn(1).width = 8;
        ws.getColumn(2).width = 40;
        ws.getColumn(3).width = 50;
        ws.getColumn(4).width = 18;
        ws.getColumn(5).width = 18;
        ws.getColumn(6).width = 15;

        let row = 1;

        function addSectionTitle(title, colSpan) {
            ws.mergeCells(row, 1, row, colSpan);
            const cell = ws.getCell(row, 1);
            cell.value = title;
            cell.fill = sectionTitleFill;
            cell.font = sectionTitleFont;
            cell.alignment = { vertical: 'middle', horizontal: 'left' };
            cell.border = darkBorder;
            ws.getRow(row).height = 32;
            row++;
        }

        function addHeaderRow(headers) {
            const r = ws.getRow(row);
            r.height = 26;
            headers.forEach((h, i) => {
                const cell = r.getCell(i + 1);
                cell.value = h;
                cell.fill = headerFill;
                cell.font = headerFont;
                cell.alignment = headerAlignment;
                cell.border = darkBorder;
            });
            row++;
        }

        function addDataRow(values) {
            const r = ws.getRow(row);
            r.height = 22;
            const isOdd = (row % 2 === 1);
            values.forEach((v, i) => {
                const cell = r.getCell(i + 1);
                cell.value = v;
                cell.font = { size: 10, name: 'Calibri', color: { argb: 'FF1F2937' } };
                cell.alignment = { vertical: 'middle', horizontal: 'center' };
                cell.border = cellBorder;
                cell.fill = isOdd ? rowFillOdd : rowFillEven;
            });
            row++;
            return r;
        }

        function addEmptyRow() { row += 2; }

        // Title
        ws.mergeCells(row, 1, row, 6);
        const titleCell = ws.getCell(row, 1);
        titleCell.value = t('Zabbix Alarm Report ({0} - {1})', startDate, endDate);
        titleCell.font = { bold: true, size: 16, color: { argb: 'FF4F46E5' }, name: 'Calibri' };
        titleCell.alignment = { vertical: 'middle', horizontal: 'center' };
        titleCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFE0E7FF' } };
        ws.getRow(row).height = 40;
        row += 2;

        const totalAlarms = data.length;
        const resolvedAlarms = data.filter(d => d.resolved).length;
        const unresolvedAlarms = totalAlarms - resolvedAlarms;
        const uniqueHosts = [...new Set(data.map(d => d.host))].length;
        const mesaiIci = data.filter(d => d.mesai === 'Mesai İçi').length;
        const mesaiDisi = totalAlarms - mesaiIci;

        // General statistics
        addSectionTitle(t('General Statistics'), 6);
        addHeaderRow([t('Statistic'), t('Value')]);
        [[t('Total Alarms'), totalAlarms], [t('Resolved Alarms'), resolvedAlarms],
        [t('Active Problems'), unresolvedAlarms], [t('Affected Devices'), uniqueHosts],
        [t('Business hours'), mesaiIci], [t('After hours'), mesaiDisi]].forEach(s => addDataRow(s));
        addEmptyRow();

        // Severity distribution
        const sevColors = {
            '5': 'FFFF4444', '4': 'FFFF8800', '3': 'FFFFAA00',
            '2': 'FFFFC800', '1': 'FF7499FF', '0': 'FF97AAB3'
        };
        addSectionTitle(t('Severity Distribution'), 6);
        addHeaderRow([t('Severity'), t('Alarm Count'), t('Share (%)')]);
        for (const sev of ['5', '4', '3', '2', '1', '0']) {
            const info = SEVERITY_MAP[sev];
            const count = data.filter(d => d.severity === sev).length;
            if (count > 0) {
                const pct = ((count / totalAlarms) * 100).toFixed(1);
                const r = addDataRow([info.name, count, `${pct}%`]);
                r.getCell(1).font = { bold: true, color: { argb: sevColors[sev] }, size: 11, name: 'Calibri' };
            }
        }
        addEmptyRow();

        // Top alarming devices
        addSectionTitle(t('Top Alarming Devices (TOP 20)'), 6);
        addHeaderRow(['#', 'Host', t('Alarm Count'), t('Share (%)')]);
        const hostCounts = {};
        data.forEach(d => hostCounts[d.host] = (hostCounts[d.host] || 0) + 1);
        Object.entries(hostCounts).sort((a, b) => b[1] - a[1]).slice(0, 20)
            .forEach(([host, count], i) => {
                addDataRow([i + 1, host, count, `${((count / totalAlarms) * 100).toFixed(1)}%`]);
            });
        addEmptyRow();

        // Host-problem combinations
        addSectionTitle(t('Host-Problem Combinations (TOP 30)'), 6);
        addHeaderRow(['#', 'Host', 'Problem', t('Alarm Count'), t('Share (%)')]);
        const comboCounts = {};
        data.forEach(d => {
            const key = `${d.host}|||${d.name}`;
            comboCounts[key] = (comboCounts[key] || 0) + 1;
        });
        Object.entries(comboCounts).sort((a, b) => b[1] - a[1]).slice(0, 30)
            .forEach(([key, count], i) => {
                const [host, problem] = key.split('|||');
                addDataRow([i + 1, host, problem, count, `${((count / totalAlarms) * 100).toFixed(1)}%`]);
            });
        addEmptyRow();

        // SLA Availability
        if (_slaDataCache && _slaDataCache.length > 0) {
            addSectionTitle(t('SLA Availability Report'), 6);
            addHeaderRow([t('SLA Name'), 'SLO (%)', 'SLI (%)', 'Uptime', 'Downtime', t('Status')]);
            [..._slaDataCache].sort((a, b) => b.sli - a.sli).forEach(item => {
                const status = item.status === 'ok' ? '✓ OK' : t('✗ Violation');
                const r = addDataRow([
                    item.slaName, `${item.slo.toFixed(2)}%`, `${item.sli.toFixed(4)}%`,
                    formatDurationLong(item.uptime), formatDurationLong(item.downtime), status
                ]);
                const sc = r.getCell(6);
                sc.font = { bold: true, color: { argb: item.status === 'ok' ? 'FF16A34A' : 'FFDC2626' }, size: 10, name: 'Calibri' };
            });
        }

        // Save
        const buffer = await workbook.xlsx.writeBuffer();
        const blob = new Blob([buffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `Zabbix_Report_${new Date().toISOString().slice(0, 10)}.xlsx`;
        a.click();
        URL.revokeObjectURL(a.href);
        showToast('Excel report downloaded', 'success');

    } catch (err) {
        console.error('Excel export error:', err);
        showToast('Excel export error: ' + err.message, 'error');
    }
}


async function exportPDF() {
    showToast('Preparing the PDF...', 'info');

    try {
        const { jsPDF } = window.jspdf;
        const pdf = new jsPDF('l', 'mm', 'a4'); // landscape
        const pageWidth = pdf.internal.pageSize.getWidth();
        const pageHeight = pdf.internal.pageSize.getHeight();
        const margin = 10;
        const usableWidth = pageWidth - 2 * margin;
        let yOffset = margin;

        // Which sections to capture (exclude detail alarm list)
        const sectionIds = [
            'generalStats',
            'severityDistribution',
            'mesaiDistribution',
            'hostProblemDistribution',
            'topDevicesSection'
        ];

        // Add SLA section if visible
        const slaSection = document.getElementById('slaSection');
        if (slaSection && slaSection.style.display !== 'none') {
            sectionIds.push('slaSection');
        }

        // Title page
        pdf.setFontSize(22);
        pdf.setTextColor(79, 70, 229);
        pdf.text(t('Zabbix Alarm Report'), pageWidth / 2, 30, { align: 'center' });

        const startDate = document.getElementById('reportStartDate').value;
        const endDate = document.getElementById('reportEndDate').value;
        pdf.setFontSize(12);
        pdf.setTextColor(100);
        pdf.text(`${startDate}  -  ${endDate}`, pageWidth / 2, 40, { align: 'center' });

        pdf.setFontSize(10);
        pdf.text(t('Generated: {0}', new Date().toLocaleString(I18N.locale)), pageWidth / 2, 48, { align: 'center' });

        yOffset = 60;

        for (const id of sectionIds) {
            const el = document.getElementById(id);
            if (!el || el.offsetParent === null) continue;

            // Get the parent section
            const section = el.closest('.report-section') || el;

            const canvas = await html2canvas(section, {
                scale: 2,
                backgroundColor: '#13131a',
                useCORS: true,
                logging: false
            });

            const imgData = canvas.toDataURL('image/png');
            const imgWidth = usableWidth;
            const imgHeight = (canvas.height * imgWidth) / canvas.width;

            // Check if fits on current page
            if (yOffset + imgHeight > pageHeight - margin) {
                pdf.addPage();
                yOffset = margin;
            }

            pdf.addImage(imgData, 'PNG', margin, yOffset, imgWidth, imgHeight);
            yOffset += imgHeight + 8;
        }

        pdf.save(`Zabbix_Report_${new Date().toISOString().slice(0, 10)}.pdf`);
        showToast('PDF downloaded', 'success');

    } catch (err) {
        console.error('PDF export error:', err);
        showToast('PDF export error: ' + err.message, 'error');
    }
}

// ===== UTILITIES =====
function formatDate(timestamp) {
    const d = new Date(timestamp * 1000);
    return d.toLocaleString(I18N.locale, {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit'
    });
}

function formatDuration(seconds) {
    if (seconds <= 0) return '-';
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;

    const u = durUnits();
    const parts = [];
    if (d > 0) parts.push(`${d}${u.d}`);
    if (h > 0) parts.push(`${h}${u.h}`);
    if (m > 0) parts.push(`${m}${u.m}`);
    if (s > 0 && d === 0) parts.push(`${s}${u.s}`);
    return parts.join(' ') || `0${u.s}`;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

async function apiRequest(endpoint, data) {
    const response = await fetch(`${API_BASE}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...data, sessionId: reportState.sessionId })
    });
    if (response.status === 401) {
        localStorage.removeItem('zabbixSession');
        showToast('Session expired. Redirecting to the sign-in page...', 'error');
        setTimeout(() => window.location.href = '/', 2000);
        throw new Error('Session expired');
    }
    const result = await response.json();
    if (!result.success) throw new Error(result.error || 'Unknown error');
    return result.data;
}

function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast ${type}`;
    clearTimeout(reportState._toastTimer);
    reportState._toastTimer = setTimeout(() => toast.classList.add('hidden'), 4000);
}

function showLoading(message) {
    document.getElementById('loadingText').textContent = message;
    document.getElementById('loadingOverlay').classList.remove('hidden');
}

function hideLoading() {
    document.getElementById('loadingOverlay').classList.add('hidden');
}

// ===== CACHE MANAGEMENT =====
async function loadCacheStats() {
    try {
        const res = await fetch(`${API_BASE}/cache-stats`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        const data = await res.json();
        if (data.success && data.data) {
            const s = data.data;
            const infoEl = document.getElementById('cacheInfoText');
            if (s.events > 0) {
                infoEl.textContent = t('📦 Cache: {0} events, {1} days, {2}', s.events.toLocaleString(I18N.locale), s.coverage_days, s.db_size_mb);
            } else {
                infoEl.textContent = '📦 Cache is empty';
            }
        }
    } catch (e) { /* ignore */ }
}

async function handleClearCache() {
    if (!confirm('Clear the cache of the active server?\n\nIts cached report data will be deleted.\nThe next report will fetch everything from Zabbix again.')) {
        return;
    }
    try {
        const res = await fetch(`${API_BASE}/cache-clear`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        const data = await res.json();
        if (data.success) {
            showToast('Cache cleared', 'success');
            loadCacheStats();
        } else {
            showToast('Could not clear the cache: ' + (data.error || ''), 'error');
        }
    } catch (e) {
        showToast('Could not clear the cache: ' + e.message, 'error');
    }
}

// ===== PREVIOUS PERIOD COMPARISON =====
// Fetches the period of the same length right before the selected range (through
// the same report job, so it is cached too) and shows what changed.
function resetComparison() {
    document.getElementById('compareBody').innerHTML =
        `<p class="section-note">${t('Fetches the period of the same length right before the selected range and shows what changed.')}</p>`;
}

async function fetchReportRows(timeFrom, timeTill) {
    const post = (path, body) => fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    }).then(r => r.json());

    const started = await post('/report-data', {
        timeFrom, timeTill,
        groupIds: Array.from(reportState.selectedGroups),
        sessionId: reportState.sessionId
    });
    if (!started.success) throw new Error(started.error || 'Could not start the job');
    for (;;) {
        await new Promise(r => setTimeout(r, 1000));
        const p = await post('/report-progress', { jobId: started.jobId });
        if (p.status === 'error') throw new Error(p.error || 'An error occurred while generating the report');
        if (p.status === 'cancelled' || p.status === 'unknown') throw new Error('Report cancelled');
        if (p.status === 'done') break;
    }
    const result = await post('/report-result', { jobId: started.jobId });
    if (!result.success) throw new Error(result.error || 'Could not fetch the result');
    return result.data;
}

function periodStats(rows) {
    const durations = rows.filter(d => d.resolved && d.duration > 0).map(d => d.duration).sort((a, b) => a - b);
    const n = durations.length;
    const bySeverity = {}, byHost = {};
    rows.forEach(d => {
        bySeverity[d.severity] = (bySeverity[d.severity] || 0) + 1;
        byHost[d.host] = (byHost[d.host] || 0) + 1;
    });
    const inHours = rows.filter(d => d.mesai === 'Mesai İçi').length;
    const resolved = rows.filter(d => d.resolved).length;
    return {
        total: rows.length, resolved, open: rows.length - resolved,
        inHours, afterHours: rows.length - inHours,
        hosts: Object.keys(byHost).length,
        avg: n ? Math.round(durations.reduce((a, b) => a + b, 0) / n) : 0,
        median: n ? (n % 2 ? durations[(n - 1) / 2] : Math.round((durations[n / 2 - 1] + durations[n / 2]) / 2)) : 0,
        bySeverity, byHost
    };
}

async function comparePrevious() {
    const range = reportState.range;
    if (!range) return;
    const btn = document.getElementById('compareBtn');
    const body = document.getElementById('compareBody');
    btn.disabled = true;
    body.innerHTML = `<p class="section-note">${t('Fetching the previous period...')}</p>`;
    try {
        const length = range.till - range.from;
        const prevFrom = range.from - length, prevTill = range.from - 1;
        const rows = await fetchReportRows(prevFrom, prevTill);
        rows.forEach(d => { d.mesai = normalizeMesai(d.mesai); });
        const prev = periodStats(rows.filter(d => reportState.selectedSeverities.has(d.severity)));
        const cur = periodStats(reportState.filteredData);
        renderComparison(cur, prev, prevFrom, prevTill);
    } catch (error) {
        resetComparison();
        showToast('Could not compare: ' + error.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

function renderComparison(cur, prev, prevFrom, prevTill) {
    const num = v => v.toLocaleString(I18N.locale);
    // goodWhenDown: fewer alarms / shorter resolution is the good direction
    const change = (c, p, goodWhenDown, fmt) => {
        const diff = c - p;
        if (!diff) return '<span style="color:var(--text-muted)">0</span>';
        const good = goodWhenDown ? diff < 0 : diff > 0;
        const sign = diff > 0 ? '+' : '−';
        const pct = p ? ` (${sign}${pctText((Math.abs(diff) / p * 100).toFixed(1))})` : '';
        return `<span style="color:${good ? '#4CAF50' : '#FF8800'};font-weight:600">${sign}${fmt(Math.abs(diff))}${pct}</span>`;
    };
    const row = (label, c, p, goodWhenDown, fmt = num) =>
        `<tr><td>${label}</td><td>${fmt(p)}</td><td>${fmt(c)}</td><td>${change(c, p, goodWhenDown, fmt)}</td></tr>`;

    let rows =
        row(t('Total Alarms'), cur.total, prev.total, true) +
        row(t('Resolved'), cur.resolved, prev.resolved, false) +
        row(t('Still open'), cur.open, prev.open, true) +
        row(t('Business hours'), cur.inHours, prev.inHours, true) +
        row(t('After hours'), cur.afterHours, prev.afterHours, true) +
        row(t('Affected hosts'), cur.hosts, prev.hosts, true) +
        row(t('Average resolution time'), cur.avg, prev.avg, true, formatDuration) +
        row(t('Median resolution time'), cur.median, prev.median, true, formatDuration);
    ['5', '4', '3', '2', '1', '0'].forEach(sev => {
        const c = cur.bySeverity[sev] || 0, p = prev.bySeverity[sev] || 0;
        if (c || p) rows += row(t(SEVERITY_MAP[sev].name), c, p, true);
    });

    const hosts = new Set([...Object.keys(cur.byHost), ...Object.keys(prev.byHost)]);
    const movers = [...hosts].map(h => ({ h, c: cur.byHost[h] || 0, p: prev.byHost[h] || 0 }))
        .filter(m => m.c !== m.p)
        .sort((a, b) => Math.abs(b.c - b.p) - Math.abs(a.c - a.p))
        .slice(0, 8);
    const moverRows = movers.map(m => row(escapeHtml(m.h), m.c, m.p, true)).join('');
    const head = first => `<thead><tr><th>${first}</th><th>${t('Previous period')}</th><th>${t('Selected period')}</th><th>${t('Change')}</th></tr></thead>`;

    document.getElementById('compareBody').innerHTML = `
        <p class="section-note">${t('Previous period: {0} – {1}', formatDate(prevFrom), formatDate(prevTill))}</p>
        <div class="table-container"><table class="report-table">${head(t('Metric'))}<tbody>${rows}</tbody></table></div>
        ${moverRows ? `<h3 style="font-size:.95rem;font-weight:600;margin:1.4rem 0 .6rem;color:var(--text-primary)">${t('Biggest changes by host')}</h3>
        <div class="table-container"><table class="report-table">${head(t('Host'))}<tbody>${moverRows}</tbody></table></div>` : ''}`;
}

// ===== BUSINESS HOURS =====
function hoursRange() {
    const hh = h => String(h).padStart(2, '0') + ':00';
    return `${hh(reportState.hours.start)} – ${hh(reportState.hours.end)}`;
}

function hoursDays() {
    const days = reportState.hours.days;
    if (days.length === 7) return t('all days');
    if (days.join() === '0,1,2,3,4') return t('weekdays');
    return days.map(d => t(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][d])).join(', ');
}
