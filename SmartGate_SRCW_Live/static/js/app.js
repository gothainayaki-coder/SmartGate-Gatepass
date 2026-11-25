// SmartGate Global JS

// ── Flash Toast Auto-Dismiss ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
    const toasts = document.querySelectorAll('.toast');
    toasts.forEach(function (toast) {
        setTimeout(function () { toast.style.opacity = '0'; toast.style.transform = 'translateX(100%)'; toast.style.transition = 'all 0.4s'; setTimeout(function () { toast.remove(); }, 400); }, 4500);
        const closeBtn = toast.querySelector('.toast-close');
        if (closeBtn) closeBtn.addEventListener('click', function () { toast.remove(); });
    });

    // ── Notification Panel Toggle ──────────────────────────────────────────
    const notifBtn = document.getElementById('notifBtn');
    const notifPanel = document.getElementById('notifPanel');
    if (notifBtn && notifPanel) {
        notifBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            notifPanel.classList.toggle('open');
            // Mark as read
            fetch('/student/notifications/read', { method: 'POST' }).catch(() => { });
            const badge = document.getElementById('notifBadge');
            if (badge) badge.style.display = 'none';
        });
        document.addEventListener('click', function (e) {
            if (!notifPanel.contains(e.target) && e.target !== notifBtn) {
                notifPanel.classList.remove('open');
            }
        });
    }

    // ── Mobile Sidebar ─────────────────────────────────────────────────────
    const hamburger = document.getElementById('hamburger');
    const sidebar = document.querySelector('.sidebar');
    const sidebarOverlay = document.querySelector('.sidebar-overlay');
    if (hamburger && sidebar) {
        hamburger.addEventListener('click', function () {
            sidebar.classList.toggle('mobile-open');
            if (sidebarOverlay) sidebarOverlay.classList.toggle('active');
        });
        if (sidebarOverlay) {
            sidebarOverlay.addEventListener('click', function () {
                sidebar.classList.remove('mobile-open');
                sidebarOverlay.classList.remove('active');
            });
        }
    }

    // ── Modal Helpers ─────────────────────────────────────────────────────
    window.openModal = function (id) {
        const el = document.getElementById(id);
        if (el) el.classList.add('open');
    };
    window.closeModal = function (id) {
        const el = document.getElementById(id);
        if (el) el.classList.remove('open');
    };
    document.querySelectorAll('.modal-overlay').forEach(function (overlay) {
        overlay.addEventListener('click', function (e) {
            if (e.target === overlay) overlay.classList.remove('open');
        });
    });

    // ── Confirm Dialogs ───────────────────────────────────────────────────
    document.querySelectorAll('[data-confirm]').forEach(function (el) {
        el.addEventListener('click', function (e) {
            const msg = el.getAttribute('data-confirm');
            if (!confirm(msg)) e.preventDefault();
        });
    });

    // ── Print Pass ────────────────────────────────────────────────────────
    window.printPass = function () {
        window.print();
    };
});

// ── QR Scanner (if available) ─────────────────────────────────────────────
function startQRScanner(videoId, resultCallbackFn) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        console.log('Camera not available');
        return;
    }
    const video = document.getElementById(videoId);
    if (!video) return;
    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
        .then(function (stream) {
            video.srcObject = stream;
            video.play();
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            function scan() {
                if (video.readyState === video.HAVE_ENOUGH_DATA) {
                    canvas.width = video.videoWidth;
                    canvas.height = video.videoHeight;
                    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                    if (typeof jsQR !== 'undefined') {
                        const code = jsQR(imageData.data, imageData.width, imageData.height);
                        if (code) { resultCallbackFn(code.data); return; }
                    }
                }
                requestAnimationFrame(scan);
            }
            scan();
        })
        .catch(function (err) { console.log('Camera error:', err); });
}

// ── Notification read for any role ───────────────────────────────────────
function markNotificationsRead(role) {
    const endpoints = {
        faculty: '/faculty/notifications/read',
        tutor: '/tutor/notifications/read',
        hod: '/hod/notifications/read',
        guard: '/guard/notifications/read',
        student: '/student/notifications/read',
    };
    const url = endpoints[role];
    if (url) fetch(url, { method: 'POST' }).catch(() => { });
    const badge = document.getElementById('notifBadge');
    if (badge) badge.style.display = 'none';
}
