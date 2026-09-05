# 非官方补丁：在 Ren'Py 明文层把台词转到本机 Bridge。
# 内嵌样式的 Hook 发出的是密文，这里不解密，只在游戏脚本还看得到原文时拦截。

init 999 python:
    import urllib.error
    import urllib.parse
    import urllib.request

    _UNOFFICIAL_BRIDGE = "http://127.0.0.1:19899/translate"
    _UNOFFICIAL_CACHE = {}

    def unofficial_bridge_translate(text):
        if not isinstance(text, str):
            return text
        source = text.strip()
        if not source:
            return text
        cached = _UNOFFICIAL_CACHE.get(source)
        if cached is not None:
            return cached
        query = urllib.parse.urlencode(
            {"from": "auto", "to": "zh", "text": source}
        )
        url = _UNOFFICIAL_BRIDGE + "?" + query
        try:
            request = urllib.request.Request(
                url, headers={"Accept": "text/plain; charset=utf-8"}
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                translated = response.read().decode("utf-8")
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            OSError,
            ValueError,
            UnicodeError,
        ):
            return text
        if not translated:
            return text
        _UNOFFICIAL_CACHE[source] = translated
        return translated

    def _install_unofficial_bridge():
        current = getattr(config, "say_menu_text_filter", None)
        if getattr(current, "_unofficial_bridge", False):
            return
        previous = current

        def chained(value):
            if callable(previous):
                try:
                    value = previous(value)
                except Exception:
                    pass
            return unofficial_bridge_translate(value)

        chained._unofficial_bridge = True
        config.say_menu_text_filter = chained

    _install_unofficial_bridge()
    if _install_unofficial_bridge not in config.interact_callbacks:
        config.interact_callbacks.append(_install_unofficial_bridge)
