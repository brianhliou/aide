/**
 * Chart.js utilities for the aide dashboard.
 *
 * Provides color palettes, default settings, chart factory functions,
 * and formatting helpers.
 */

const AIDE_COLORS = {
    primary:   "rgb(59, 130, 246)",   // blue-500
    secondary: "rgb(16, 185, 129)",   // emerald-500
    accent:    "rgb(245, 158, 11)",   // amber-500
    danger:    "rgb(239, 68, 68)",    // red-500
    purple:    "rgb(139, 92, 246)",   // violet-500
    pink:      "rgb(236, 72, 153)",   // pink-500
    cyan:      "rgb(6, 182, 212)",    // cyan-500
    indigo:    "rgb(99, 102, 241)",   // indigo-500
    teal:      "rgb(20, 184, 166)",   // teal-500
    orange:    "rgb(249, 115, 22)",   // orange-500
    lime:      "rgb(132, 204, 22)",   // lime-500
    rose:      "rgb(244, 63, 94)",    // rose-500
};

// Alpha variants for fills
const AIDE_COLORS_ALPHA = {
    primary:   "rgba(59, 130, 246, 0.1)",
    secondary: "rgba(16, 185, 129, 0.1)",
    accent:    "rgba(245, 158, 11, 0.1)",
    danger:    "rgba(239, 68, 68, 0.1)",
    purple:    "rgba(139, 92, 246, 0.1)",
};

const PROJECT_COLORS = [
    "rgb(59, 130, 246)",   // blue
    "rgb(16, 185, 129)",   // emerald
    "rgb(245, 158, 11)",   // amber
    "rgb(239, 68, 68)",    // red
    "rgb(139, 92, 246)",   // violet
    "rgb(236, 72, 153)",   // pink
    "rgb(6, 182, 212)",    // cyan
    "rgb(249, 115, 22)",   // orange
    "rgb(99, 102, 241)",   // indigo
    "rgb(20, 184, 166)",   // teal
    "rgb(132, 204, 22)",   // lime
    "rgb(244, 63, 94)",    // rose
];

const CHART_DEFAULTS = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
        legend: {
            labels: {
                font: { size: 12, family: "system-ui, sans-serif" },
                padding: 16,
            },
        },
        tooltip: {
            titleFont: { size: 13, family: "system-ui, sans-serif" },
            bodyFont: { size: 12, family: "system-ui, sans-serif" },
            padding: 10,
            cornerRadius: 6,
        },
    },
    scales: {
        x: {
            ticks: { font: { size: 11, family: "system-ui, sans-serif" } },
            grid: { display: false },
        },
        y: {
            ticks: { font: { size: 11, family: "system-ui, sans-serif" } },
            grid: { color: "rgba(0, 0, 0, 0.05)" },
        },
    },
};


// --- Chart factory functions ---

/**
 * Create a line chart.
 * @param {string} canvasId - The canvas element ID.
 * @param {string[]} labels - X-axis labels.
 * @param {object[]} datasets - Chart.js dataset objects (label, data, borderColor, etc.).
 * @param {object} [options] - Additional Chart.js options to merge.
 * @returns {Chart}
 */
function createLineChart(canvasId, labels, datasets, options) {
    const ctx = document.getElementById(canvasId).getContext("2d");
    const merged = deepMerge({}, CHART_DEFAULTS, options || {});
    return new Chart(ctx, {
        type: "line",
        data: { labels, datasets },
        options: merged,
    });
}

/**
 * Create a vertical bar chart.
 * @param {string} canvasId
 * @param {string[]} labels
 * @param {number[]} data
 * @param {object} [options]
 * @returns {Chart}
 */
function createBarChart(canvasId, labels, data, options) {
    const ctx = document.getElementById(canvasId).getContext("2d");
    const merged = deepMerge({}, CHART_DEFAULTS, options || {});
    return new Chart(ctx, {
        type: "bar",
        data: {
            labels,
            datasets: [{
                data,
                backgroundColor: AIDE_COLORS.primary,
                borderRadius: 4,
            }],
        },
        options: Object.assign(merged, {
            plugins: Object.assign(merged.plugins || {}, {
                legend: { display: false },
            }),
        }),
    });
}

/**
 * Create a horizontal bar chart.
 * @param {string} canvasId
 * @param {string[]} labels
 * @param {number[]} data
 * @param {object} [options]
 * @returns {Chart}
 */
function createHorizontalBarChart(canvasId, labels, data, options) {
    const ctx = document.getElementById(canvasId).getContext("2d");
    const merged = deepMerge({}, CHART_DEFAULTS, options || {});
    merged.indexAxis = "y";
    if (!merged.plugins) merged.plugins = {};
    merged.plugins.legend = { display: false };
    return new Chart(ctx, {
        type: "bar",
        data: {
            labels,
            datasets: [{
                data,
                backgroundColor: AIDE_COLORS.primary,
                borderRadius: 4,
            }],
        },
        options: merged,
    });
}

/**
 * Create a pie or doughnut chart.
 * @param {string} canvasId
 * @param {string[]} labels
 * @param {number[]} data
 * @param {object} [options] - Set options.cutout for doughnut (e.g., "60%").
 * @returns {Chart}
 */
function createPieChart(canvasId, labels, data, options) {
    const ctx = document.getElementById(canvasId).getContext("2d");
    const type = (options && options.cutout) ? "doughnut" : "pie";
    const merged = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                position: "bottom",
                labels: {
                    font: { size: 12, family: "system-ui, sans-serif" },
                    padding: 16,
                },
            },
            tooltip: CHART_DEFAULTS.plugins.tooltip,
        },
    };
    if (options && options.cutout) merged.cutout = options.cutout;
    return new Chart(ctx, {
        type,
        data: {
            labels,
            datasets: [{
                data,
                backgroundColor: PROJECT_COLORS.slice(0, labels.length),
                borderWidth: 1,
                borderColor: "#fff",
            }],
        },
        options: Object.assign(merged, options || {}),
    });
}

/**
 * Create a scatter chart.
 * @param {string} canvasId
 * @param {object[]} datasets - Chart.js scatter datasets ({label, data: [{x, y}], ...}).
 * @param {object} [options]
 * @returns {Chart}
 */
function createScatterChart(canvasId, datasets, options) {
    const ctx = document.getElementById(canvasId).getContext("2d");
    const merged = deepMerge({}, CHART_DEFAULTS, options || {});
    return new Chart(ctx, {
        type: "scatter",
        data: { datasets },
        options: merged,
    });
}


// --- Formatting helpers ---

/**
 * Format a dollar amount: "$1.23"
 * @param {number} value
 * @returns {string}
 */
function formatCost(value) {
    return "$" + (value || 0).toFixed(2);
}

/**
 * Format token count: "48K" or "1.2M"
 * @param {number} value
 * @returns {string}
 */
function formatTokens(value) {
    if (value == null) return "0";
    if (value >= 1_000_000) {
        return (value / 1_000_000).toFixed(1) + "M";
    }
    if (value >= 1_000) {
        return (value / 1_000).toFixed(0) + "K";
    }
    return value.toString();
}

/**
 * Format duration in seconds: "45m" or "1h 12m"
 * @param {number} seconds
 * @returns {string}
 */
function formatDuration(seconds) {
    if (seconds == null || seconds <= 0) return "0m";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (h > 0) {
        return m > 0 ? h + "h " + m + "m" : h + "h";
    }
    return Math.max(m, 1) + "m";
}


// --- Internal helpers ---

/**
 * Deep merge objects (simple recursive, handles plain objects only).
 */
function deepMerge(target, ...sources) {
    for (const source of sources) {
        if (!source) continue;
        for (const key of Object.keys(source)) {
            if (
                source[key] &&
                typeof source[key] === "object" &&
                !Array.isArray(source[key]) &&
                target[key] &&
                typeof target[key] === "object" &&
                !Array.isArray(target[key])
            ) {
                deepMerge(target[key], source[key]);
            } else {
                target[key] = source[key];
            }
        }
    }
    return target;
}
