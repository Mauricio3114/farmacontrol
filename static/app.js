async function registrarPush() {
    if (!('serviceWorker' in navigator)) {
        console.log("Service Worker não suportado");
        return;
    }

    try {
        // registra o SW
        const registration = await navigator.serviceWorker.register('/sw.js');

        // pede permissão
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') {
            alert("Permissão negada");
            return;
        }

        // tua chave pública
        const vapidPublicKey = "BMw7PhwT4nvbjd4gBnR2wq86Dj5U1rJ7lzsKtY0VaCnNCm408z2Mh0rgDB4hu_STN3jl49vtcp5UeaWmhVWYlb8";

        const convertedKey = urlBase64ToUint8Array(vapidPublicKey);

        // cria inscrição
        const subscription = await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: convertedKey
        });

        // envia pro backend
        await fetch('/subscribe', {
            method: 'POST',
            body: JSON.stringify(subscription),
            headers: {
                'Content-Type': 'application/json'
            }
        });

        console.log("Push registrado com sucesso");

    } catch (error) {
        console.error("Erro no push:", error);
    }
}

// função auxiliar
function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = atob(base64);
    return Uint8Array.from([...rawData].map(char => char.charCodeAt(0)));
}

// chama automaticamente
registrarPush();