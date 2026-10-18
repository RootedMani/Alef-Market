"""Services: the only layer that combines storage, data sources, pure core and the LLM.

One function per user action: fetch inputs -> call pure core -> phrase with the gateway ->
save -> return a contract. API routes and HTML pages both call services, never the core
directly, so the JSON API and the pages can't drift apart.
"""
