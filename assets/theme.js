/* Local browser presentation only. No network, identity, or report requests. */
(function () {
    "use strict";

    var STORAGE_KEY = "workspace.theme";

    function isScheme(value) {
        return value === "light" || value === "dark";
    }

    function isObject(value) {
        return value !== null && typeof value === "object" && !Array.isArray(value);
    }

    function clickCount(value) {
        return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : 0;
    }

    function preference() {
        var saved;
        try {
            saved = window.localStorage.getItem(STORAGE_KEY);
        } catch (ignore) {
            // Storage can be unavailable in restricted/private browsing.
        }
        if (isScheme(saved)) {
            return {colorScheme: saved, explicit: true, clicks: 0};
        }
        var dark = false;
        try {
            dark = Boolean(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
        } catch (ignore) {
            // A missing or restricted media-query API defaults to light.
        }
        return {colorScheme: dark ? "dark" : "light", explicit: false, clicks: 0};
    }

    function applyScheme(scheme) {
        if (document.documentElement) {
            document.documentElement.setAttribute("data-theme", scheme);
        }
    }

    function sync(nClicks, state, providerTheme) {
        var valid = isObject(state) && isScheme(state.colorScheme) &&
            typeof state.explicit === "boolean" && clickCount(state.clicks) === state.clicks;
        var current = valid ? state : preference();
        var clicks = clickCount(nClicks);
        // Count deltas handle repeated calls and batched rapid clicks without
        // reversing a theme twice for the same event. No initial-load write.
        var delta = Math.max(0, clicks - current.clicks);
        var scheme = current.colorScheme;
        if (delta % 2 === 1) {
            scheme = scheme === "dark" ? "light" : "dark";
        }
        if (delta > 0) {
            try {
                window.localStorage.setItem(STORAGE_KEY, scheme);
            } catch (ignore) {
                // The selected theme still works for this page if saving fails.
            }
        }
        applyScheme(scheme);
        var theme = Object.assign({}, isObject(providerTheme) ? providerTheme : {}, {colorScheme: scheme});
        var next = {colorScheme: scheme, explicit: current.explicit || delta > 0, clicks: clicks};
        var label = scheme === "dark" ? "Switch to light mode" : "Switch to dark mode";
        return [theme, next, scheme === "dark" ? "\u2600" : "\u263e", label,
            scheme === "dark" ? "true" : "false", label];
    }

    function figure(themeState, graphId, source) {
        if (graphId !== "performance-chart" || !isObject(source)) {
            return window.dash_clientside.no_update;
        }
        var scheme = isObject(themeState) && isScheme(themeState.colorScheme) ?
            themeState.colorScheme : preference().colorScheme;
        var dark = scheme === "dark";
        var colors = dark ? {
            background: "#18283f", text: "#d9e5f5", grid: "#344961", axis: "#8ea5c2"
        } : {
            background: "#ffffff", text: "#596d87", grid: "#e4ebf5", axis: "#9cabc1"
        };
        // CSS tokens are the source of truth when the DOM and Store agree.
        // Fallbacks also support the first dynamic mount and blocked CSS APIs.
        try {
            if (document.documentElement.getAttribute("data-theme") === scheme) {
                var style = window.getComputedStyle(document.documentElement);
                Object.keys(colors).forEach(function (key) {
                    var value = style.getPropertyValue("--chart-" + key).trim();
                    if (value) { colors[key] = value; }
                });
            }
        } catch (ignore) {}

        var output = Object.assign({}, source);
        var layout = Object.assign({}, isObject(source.layout) ? source.layout : {});
        // Clone only presentation objects. Trace data, ranges, category order,
        // uirevision, annotations and other business/chart configuration survive.
        layout.paper_bgcolor = colors.background;
        layout.plot_bgcolor = colors.background;
        layout.font = Object.assign({}, layout.font || {}, {color: colors.text});
        layout.legend = Object.assign({}, layout.legend || {}, {
            bgcolor: colors.background,
            font: Object.assign({}, (layout.legend || {}).font || {}, {color: colors.text})
        });
        ["xaxis", "yaxis"].forEach(function (key) {
            var axis = Object.assign({}, layout[key] || {});
            axis.color = colors.axis;
            axis.gridcolor = colors.grid;
            axis.linecolor = colors.grid;
            axis.zerolinecolor = colors.grid;
            axis.tickfont = Object.assign({}, axis.tickfont || {}, {color: colors.text});
            if (isObject(axis.title)) {
                axis.title = Object.assign({}, axis.title, {
                    font: Object.assign({}, axis.title.font || {}, {color: colors.text})
                });
            }
            layout[key] = axis;
        });
        var template = Object.assign({}, isObject(layout.template) ? layout.template : {});
        template.layout = Object.assign({}, template.layout || {}, {
            paper_bgcolor: colors.background, plot_bgcolor: colors.background,
            font: Object.assign({}, (template.layout || {}).font || {}, {color: colors.text})
        });
        layout.template = template;
        output.layout = layout;
        return output;
    }

    // Dash loads local assets before mounting its React shell. Apply the saved
    // choice immediately, without waiting for a callback or observing the DOM.
    applyScheme(preference().colorScheme);
    window.dash_clientside = Object.assign({}, window.dash_clientside || {});
    window.dash_clientside.workspace_theme = {sync: sync, figure: figure};
}());
