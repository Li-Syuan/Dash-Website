/* Report IDs only. Browser preferences never grant access or choose a route. */
(function () {
    "use strict";
    var MAX_IDS = 100;
    var MAX_BYTES = 40000;
    var currentScope = null;
    var currentAllowed = new Set();
    var memory = {};
    var identifier = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
    var scopePattern = /^[a-f0-9]{64}$/;
    var prefix = "workspace.catalog.";

    function validScope(scope) {
        return typeof scope === "string" && scopePattern.test(scope);
    }
    function cleanIds(values, allowed) {
        if (!Array.isArray(values)) { return []; }
        var seen = new Set();
        return values.slice(0, MAX_IDS).filter(function (id) {
            if (typeof id !== "string" || !identifier.test(id) || !allowed.has(id) || seen.has(id)) {
                return false;
            }
            seen.add(id);
            return true;
        });
    }
    function clean(value, scope, allowed) {
        value = value && typeof value === "object" && !Array.isArray(value) ? value : {};
        return {scope: scope, favorites: cleanIds(value.favorites, allowed), recent: cleanIds(value.recent, allowed)};
    }
    function read(scope, allowed) {
        var value = memory[scope] || {};
        try {
            var raw = window.localStorage.getItem(prefix + scope);
            if (raw && raw.length <= MAX_BYTES) { value = JSON.parse(raw); }
        } catch (ignore) { /* Private mode, blocked storage, or malformed data: use memory. */ }
        return clean(value, scope, allowed);
    }
    function save(value) {
        // The scoped key partitions users; the stored value contains only bounded ID lists.
        var stored = {favorites: value.favorites, recent: value.recent};
        memory[value.scope] = stored;
        try { window.localStorage.setItem(prefix + value.scope, JSON.stringify(stored)); }
        catch (ignore) { /* Favorites still work for this page when storage is unavailable. */ }
    }
    function same(left, right) {
        return left && JSON.stringify(left) === JSON.stringify(right);
    }
    function triggeredFavorite(context) {
        var triggers = context && context.triggered;
        if (!Array.isArray(triggers) || !triggers.length) { return null; }
        for (var i = 0; i < triggers.length; i += 1) {
            var item = triggers[i];
            if (!item || typeof item.prop_id !== "string" || !item.prop_id.endsWith(".n_clicks")) { continue; }
            try {
                var id = JSON.parse(item.prop_id.slice(0, -9));
                if (id.type !== "catalog-favorite" || !currentAllowed.has(id.report)) { continue; }
                // Dash supplies the actual triggering value. Inserted/remounted buttons have zero clicks.
                var clicks = item.value;
                if (typeof clicks !== "number") {
                    var group = context.inputs_list && context.inputs_list[0];
                    var matched = Array.isArray(group) && group.find(function (input) {
                        return input.id && input.id.report === id.report;
                    });
                    clicks = matched && matched.value;
                }
                if (typeof clicks === "number" && Number.isFinite(clicks) && clicks > 0) { return id.report; }
            } catch (ignore) { /* A malformed trigger never toggles a preference. */ }
        }
        return null;
    }
    function preferences(clicks, scopeData, previous) {
        var noUpdate = window.dash_clientside.no_update;
        if (!scopeData || !validScope(scopeData.scope) || !Array.isArray(scopeData.allowed_ids)) {
            currentScope = null;
            currentAllowed = new Set();
            return noUpdate;
        }
        currentScope = scopeData.scope;
        currentAllowed = new Set(scopeData.allowed_ids.filter(function (id) {
            return typeof id === "string" && identifier.test(id);
        }));
        var value = read(currentScope, currentAllowed);
        var report = triggeredFavorite(window.dash_clientside.callback_context);
        if (report) {
            if (value.favorites.indexOf(report) >= 0) {
                value.favorites = value.favorites.filter(function (id) { return id !== report; });
            } else {
                value.favorites = [report].concat(value.favorites).slice(0, MAX_IDS);
            }
        }
        save(value);
        return same(value, previous) ? noUpdate : value;
    }

    window.dash_clientside = Object.assign({}, window.dash_clientside, {
        workspace_catalog: {preferences: preferences}
    });

    // Capture before normal anchor navigation, including keyboard activation.
    // Link metadata comes from server-rendered authorized PageSpec entries.
    document.addEventListener("click", function (event) {
        var target = event.target && event.target.closest && event.target.closest("a[data-report-id][data-report-scope]");
        if (!target || !validScope(currentScope) || target.getAttribute("data-report-scope") !== currentScope) { return; }
        var id = target.getAttribute("data-report-id");
        if (!currentAllowed.has(id)) { return; }
        var value = read(currentScope, currentAllowed);
        value.recent = [id].concat(value.recent.filter(function (item) { return item !== id; })).slice(0, MAX_IDS);
        save(value);
    }, true);
}());
