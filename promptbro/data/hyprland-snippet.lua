-- PromptBro: paste into ~/.config/hypr/hyprland.lua and bindings.lua yourself.
-- This is a template: run `promptbro --print-hypr-snippet` to get these lines with
-- the absolute path to your install already filled in.
hl.layer_rule({ match = { namespace = "^promptbro$" }, animation = "fade" })
-- hide the prompter from screen shares (and your own screenshots)
hl.layer_rule({ match = { namespace = "^promptbro$" }, no_screen_share = true })

o.bind("SUPER + SHIFT + T",            "PromptBro toggle",  "/path/to/promptbro/bin/promptbro toggle")
o.bind("SUPER + SHIFT + backslash",    "PromptBro pause",   "/path/to/promptbro/bin/promptbro pause")
o.bind("SUPER + SHIFT + bracketright", "PromptBro next",    "/path/to/promptbro/bin/promptbro next")
o.bind("SUPER + SHIFT + bracketleft",  "PromptBro prev",    "/path/to/promptbro/bin/promptbro prev")
o.bind("SUPER + SHIFT + Home",         "PromptBro restart", "/path/to/promptbro/bin/promptbro restart")
