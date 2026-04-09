class ChatCrypto {
    static ALGO = {
        name: "RSA-OAEP",
        modulusLength: 2048,
        publicExponent: new Uint8Array([1, 0, 1]),
        hash: "SHA-256",
    };

    static async generateKeys() {
        const keyPair = await window.crypto.subtle.generateKey(
            this.ALGO,
            true,
            ["encrypt", "decrypt"]
        );
        const publicKeyJWK = await window.crypto.subtle.exportKey("jwk", keyPair.publicKey);
        const privateKeyJWK = await window.crypto.subtle.exportKey("jwk", keyPair.privateKey);
        return { publicKeyJWK, privateKeyJWK };
    }

    static async importPublicKey(jwkObj) {
        return await window.crypto.subtle.importKey(
            "jwk",
            jwkObj,
            this.ALGO,
            true,
            ["encrypt"]
        );
    }

    static async importPrivateKey(jwkObj) {
        return await window.crypto.subtle.importKey(
            "jwk",
            jwkObj,
            this.ALGO,
            true,
            ["decrypt"]
        );
    }

    static async encryptData(text, jwkObj) {
        const key = await this.importPublicKey(jwkObj);
        const encoded = new TextEncoder().encode(text);
        const encrypted = await window.crypto.subtle.encrypt(this.ALGO, key, encoded);
        return this.arrayBufferToBase64(encrypted);
    }

    static async decryptData(base64Str, privateJwkObj) {
        try {
            const key = await this.importPrivateKey(privateJwkObj);
            const buffer = this.base64ToArrayBuffer(base64Str);
            const decrypted = await window.crypto.subtle.decrypt(this.ALGO, key, buffer);
            return new TextDecoder().decode(decrypted);
        } catch(e) {
            console.error("Decryption failed", e);
            return `[Decryption Error: ${e.message || e.name || "Unknown RSA-OAEP failure"}]`;
        }
    }

    static arrayBufferToBase64(buffer) {
        let binary = '';
        const bytes = new Uint8Array(buffer);
        const len = bytes.byteLength;
        for (let i = 0; i < len; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return window.btoa(binary);
    }

    static base64ToArrayBuffer(base64) {
        const binary_string = window.atob(base64);
        const len = binary_string.length;
        const bytes = new Uint8Array(len);
        for (let i = 0; i < len; i++) {
            bytes[i] = binary_string.charCodeAt(i);
        }
        return bytes.buffer;
    }

    // Initialize keys for the logged-in user if they don't exist
    static async setupKeys(userId) {
        const privKeyKey = `chat_priv_${userId}`;
        const pubKeyKey = `chat_pub_${userId}`;
        const syncKey = `chat_synced_${userId}`;
        
        let privJWK = localStorage.getItem(privKeyKey);
        let pubJWK = localStorage.getItem(pubKeyKey);

        if (!privJWK || !pubJWK) {
            console.log("Generating new E2EE keys...");
            const keys = await this.generateKeys();
            privJWK = JSON.stringify(keys.privateKeyJWK);
            pubJWK = JSON.stringify(keys.publicKeyJWK);
            
            localStorage.setItem(privKeyKey, privJWK);
            localStorage.setItem(pubKeyKey, pubJWK);
            sessionStorage.removeItem(syncKey); // Force re-sync
        }

        // Always check if we need to sync this session to ensure server has current public key
        if (!sessionStorage.getItem(syncKey)) {
            console.log("Syncing E2EE public key with server...");
            try {
                await fetch('/api/keys/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ public_key: pubJWK })
                });
                sessionStorage.setItem(syncKey, 'true');
            } catch (e) {
                console.error("Failed to sync key", e);
            }
        }

        return {
            privateKey: JSON.parse(privJWK),
            publicKey: JSON.parse(pubJWK)
        };
    }
}

window.ChatCrypto = ChatCrypto;
