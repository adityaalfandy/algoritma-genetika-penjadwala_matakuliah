document.addEventListener('DOMContentLoaded', () => {
    const body = document.getElementById('app-body');
    const toggleButton = document.getElementById('theme-toggle');
    const lightModeClass = 'light-theme'; 
    const storageKey = 'app-theme';

    // 1. Fungsi untuk mengatur tema
    const setTheme = (isLight) => {
        if (isLight) {
            body.classList.add(lightModeClass);
            toggleButton.innerHTML = '🌙 Mode Gelap';
            localStorage.setItem(storageKey, 'light');
        } else {
            body.classList.remove(lightModeClass);
            toggleButton.innerHTML = '☀️ Mode Terang';
            localStorage.setItem(storageKey, 'dark');
        }
    };

    // 2. Cek Preferensi yang Tersimpan (saat halaman dimuat)
    const savedTheme = localStorage.getItem(storageKey);
    
    if (savedTheme) {
        setTheme(savedTheme === 'light');
    } else {
        // Cek preferensi sistem operasi
        const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
        setTheme(!prefersDark); 
    }

    // 3. Event Listener untuk Tombol Toggle
    toggleButton.addEventListener('click', () => {
        const isCurrentlyLight = body.classList.contains(lightModeClass);
        // Atur ke tema yang berlawanan
        setTheme(!isCurrentlyLight);
    });
});