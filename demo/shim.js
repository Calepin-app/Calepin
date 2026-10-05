// Démo en ligne : charge Pyodide et le code de Calepin, puis répond aux appels /api/... de la page
// sans serveur. DEMO (textes, langue, version de Pyodide) est inséré dans la page par build.py.
(function () {
  const PAGES = {en: './', fr: 'fr.html'};
  try {  // langue choisie lors d'une visite précédente, sinon celle du navigateur
    const want = localStorage.getItem('calepinDemoLang') || (navigator.language.startsWith('fr') ? 'fr' : 'en');
    if (want !== DEMO.lang && PAGES[want]) { location.replace(PAGES[want]); return; }
  } catch (e) {}

  let py;
  const ready = (async () => {
    const pyodide = await loadPyodide();
    const files = await (await fetch('bundle.json')).json();
    for (const [path, b64] of Object.entries(files)) {
      pyodide.FS.mkdirTree(path.slice(0, path.lastIndexOf('/')));
      pyodide.FS.writeFile(path, Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
    }
    pyodide.runPython("import sys; sys.path.insert(0, '/calepin/demo')");
    py = pyodide.pyimport('demo');
    py.start(DEMO.lang);
  })();

  const realFetch = window.fetch.bind(window);
  window.fetch = async (url, opts = {}) => {
    const u = url instanceof Request ? url.url : String(url);  // Pyodide passe aussi des objets URL
    if (!u.startsWith('/api/')) return realFetch(url, opts);
    if (u === '/api/langue') {  // une page par langue : on y va, la page n'a pas à se recharger
      const lang = JSON.parse(opts.body).lang;
      try { localStorage.setItem('calepinDemoLang', lang); } catch (e) {}
      location.replace(PAGES[lang]);
      return new Promise(() => {});
    }
    await ready;
    const [status, type, body] = JSON.parse(py.handle(opts.method || 'GET', u, opts.body || ''));
    return new Response(body, {status, headers: {'Content-Type': type}});
  };

  document.addEventListener('DOMContentLoaded', () => {
    const bar = document.createElement('div');
    bar.id = 'demobar';
    bar.innerHTML = `<span></span> <a href="https://github.com/Calepin-app/Calepin#readme"></a>`;
    bar.firstChild.textContent = DEMO.banner;
    bar.lastChild.textContent = DEMO.install + ' →';
    const wait = document.createElement('div');
    wait.id = 'demowait';
    wait.textContent = DEMO.loading;
    document.body.prepend(bar, wait);
    ready.then(() => wait.remove(), err => { wait.textContent = DEMO.failed; console.error(err); });
  });
})();
