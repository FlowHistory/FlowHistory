# ADR 0019: Tooltip Component for Settings

## Status
Implemented

## Context

The settings page has many configuration options (scheduled backups, file watcher, retention, restore, etc.) with labels that may not be self-explanatory to new users. Some fields have `help_text` shown below the input, but this approach doesn't work well for checkbox fields (which currently don't display help text at all) and can make the form feel cluttered when every field has a description paragraph.

A tooltip component would provide contextual help on hover/focus without adding visual noise, giving users quick explanations of what each setting does and why they might change it.

## Decision

### 1. Tooltip Template Component

Create `backup/templates/backup/components/_tooltip.html`:

```html
<span class="tooltip-trigger group relative inline-flex cursor-help ml-1">
  <svg class="h-4 w-4 text-gray-400 dark:text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
    <circle cx="12" cy="12" r="10" />
    <path d="M12 16v-4M12 8h.01" />
  </svg>
  <span class="tooltip-content invisible absolute bottom-full left-1/2 z-50 mb-2 w-56 -translate-x-1/2 rounded-md bg-gray-900 px-3 py-2 text-xs text-white shadow-lg group-hover:visible dark:bg-gray-700">
    {{ text }}
    <span class="absolute left-1/2 top-full -translate-x-1/2 border-4 border-transparent border-t-gray-900 dark:border-t-gray-700"></span>
  </span>
</span>
```

The component uses CSS-only hover via Tailwind's `group`/`group-hover` — no JavaScript required.

### 2. Integration with Form Fields

Update `_form_field.html` to accept an optional `tooltip` parameter and render the icon next to the label:

**Checkbox fields:**
```html
<div class="flex items-center gap-2">
  {{ field }}
  <label for="{{ field.id_for_label }}" class="text-sm font-medium text-gray-700 dark:text-gray-300">
    {{ field.label }}
    {% if tooltip %}{% include "backup/components/_tooltip.html" with text=tooltip %}{% endif %}
  </label>
</div>
```

**Standard fields:**
```html
<div>
  <label for="{{ field.id_for_label }}" class="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
    {{ field.label }}
    {% if tooltip %}{% include "backup/components/_tooltip.html" with text=tooltip %}{% endif %}
  </label>
  {{ field }}
  ...
</div>
```

### 3. Usage in Settings Template

Pass tooltip text via the `with` keyword on form field includes:

```html
{% include "backup/components/_form_field.html" with field=form.is_active tooltip="When enabled, backups run automatically on the configured schedule." %}
{% include "backup/components/_form_field.html" with field=form.always_backup tooltip="Normally, scheduled backups are skipped if flows.json hasn't changed. Enable this to always create a backup regardless." %}
{% include "backup/components/_form_field.html" with field=form.watch_debounce_seconds tooltip="After a file change is detected, the watcher waits this many seconds for additional changes before creating a backup. Prevents duplicate backups from rapid saves." %}
```

### 4. Tooltip Text for Each Setting

| Field | Tooltip |
|-------|---------|
| Instance Name | A friendly name to identify this Node-RED instance in the dashboard. |
| Flows File Path | Absolute path to the flows.json file inside the Node-RED container. |
| Enable Scheduled Backups | When enabled, backups run automatically on the configured schedule. |
| Always Create Scheduled Backups | Normally, scheduled backups are skipped if flows.json hasn't changed. Enable this to always create a backup regardless. |
| Backup Frequency | How often scheduled backups run. Daily runs once per day; Weekly runs on the selected day. |
| Backup Time | The time of day when scheduled backups are created (24-hour format). |
| Day of Week | Which day weekly backups run on. Ignored when frequency is set to Daily. |
| Enable File Watcher | Monitors flows.json for changes and creates a backup automatically when the file is modified. |
| Debounce (seconds) | After a file change is detected, the watcher waits this many seconds for additional changes before creating a backup. Prevents duplicate backups from rapid saves. |
| Include flows_cred.json | Include the Node-RED credentials file in backups. Contains encrypted passwords and API keys used by your flows. |
| Include settings.js | Include the Node-RED settings file in backups. Contains runtime configuration like port, authentication, and logging. |
| Maximum Backups | The total number of backups to keep. When exceeded, the oldest unpinned backups are deleted first. |
| Maximum Age (days) | Unpinned backups older than this are automatically deleted during retention cleanup. |
| Restart Node-RED After Restore | Automatically restart the Node-RED container after restoring a backup so changes take effect immediately. |
| Container Name | The Docker container name for Node-RED. Used to restart the container after a restore. |

### 5. Tailwind CSS

Add extracted tooltip classes to `input.css` to keep the component DRY:

```css
@layer components {
  .tooltip-trigger {
    @apply group relative inline-flex cursor-help ml-1;
  }
  .tooltip-content {
    @apply invisible absolute bottom-full left-1/2 z-50 mb-2 w-56 -translate-x-1/2 rounded-md bg-gray-900 px-3 py-2 text-xs text-white shadow-lg group-hover:visible dark:bg-gray-700;
  }
}
```

### 6. Accessibility

- The tooltip icon uses `aria-label="Help"` and `role="img"` for screen readers.
- Tooltip content is also visible on `:focus-within` (not just hover) so keyboard users can access it by tabbing to the icon.
- Add `tabindex="0"` to the trigger `<span>` so it is focusable.

## Consequences

### Positive
- Users get contextual help without leaving the settings page
- No JavaScript required — pure CSS hover/focus approach
- Reusable component available for any future page that needs inline help
- Checkbox fields gain help text capability they currently lack

### Negative
- Tooltip positioning may need adjustment on mobile (small screens could clip the popup)
- Adds a small visual element (info icon) next to every label, which could feel busy if overused — limit to settings page initially
