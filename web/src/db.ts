// The local library, in IndexedDB. Records, vectors and thumbnails live in
// one database so an item is written in a single transaction: either all of
// it is stored or none of it is.

export type Kind = "image" | "note";

export interface Item {
    /** SHA-256 of the original file's bytes, hex. */
    id: string;
    kind: Kind;
    name: string;
    /** Path within a chosen folder, when there was one. */
    path: string;
    type: string;
    size: number;
    /** The file's last-modified time, milliseconds since the epoch. */
    modified: number;
    /** When it was added to this library. */
    added: number;
    width?: number;
    height?: number;
    /** Note text, searched by BM25. Empty for images until OCR exists. */
    text: string;
    excerpt: string;
    /** The vector space the vectors below belong to; see config.ts. */
    space: string;
}

export interface Vectors {
    id: string;
    /** CLIP image vector, images only. */
    image?: Float32Array;
    /** MiniLM text vector, notes only. */
    text?: Float32Array;
}

const NAME = "metabare";
const VERSION = 1;

export class QuotaError extends Error {}

function request<T>(r: IDBRequest<T>): Promise<T> {
    return new Promise((resolve, reject) => {
        r.onsuccess = () => resolve(r.result);
        r.onerror = () => reject(r.error);
    });
}

function done(tx: IDBTransaction): Promise<void> {
    return new Promise((resolve, reject) => {
        tx.oncomplete = () => resolve();
        tx.onabort = () =>
            reject(
                tx.error?.name === "QuotaExceededError"
                    ? new QuotaError("storage is full")
                    : tx.error,
            );
        tx.onerror = () => undefined;
    });
}

export class Library {
    private constructor(private db: IDBDatabase) {}

    static async open(name = NAME): Promise<Library> {
        const open = indexedDB.open(name, VERSION);
        open.onupgradeneeded = () => {
            const db = open.result;
            db.createObjectStore("items", { keyPath: "id" });
            db.createObjectStore("vectors", { keyPath: "id" });
            db.createObjectStore("thumbs");
        };
        return new Library(await request(open));
    }

    close() {
        this.db.close();
    }

    async has(id: string): Promise<boolean> {
        const tx = this.db.transaction("items");
        return (await request(tx.objectStore("items").count(id))) > 0;
    }

    async put(item: Item, vectors: Vectors, thumb: Blob | null): Promise<void> {
        const tx = this.db.transaction(["items", "vectors", "thumbs"], "readwrite");
        tx.objectStore("items").put(item);
        tx.objectStore("vectors").put(vectors);
        if (thumb) tx.objectStore("thumbs").put(thumb, item.id);
        await done(tx);
    }

    async items(): Promise<Item[]> {
        return request(this.db.transaction("items").objectStore("items").getAll());
    }

    async vectors(): Promise<Vectors[]> {
        return request(this.db.transaction("vectors").objectStore("vectors").getAll());
    }

    /** Every item, vector and thumbnail, read in one transaction so they agree. */
    async snapshot(): Promise<{ items: Item[]; vectors: Vectors[]; thumbs: Map<string, Blob> }> {
        const tx = this.db.transaction(["items", "vectors", "thumbs"]);
        const thumbs = new Map<string, Blob>();
        const store = tx.objectStore("thumbs");
        const [items, vectors, keys, blobs] = await Promise.all([
            request(tx.objectStore("items").getAll()),
            request(tx.objectStore("vectors").getAll()),
            request(store.getAllKeys()),
            request(store.getAll()),
        ]);
        keys.forEach((k, i) => thumbs.set(String(k), blobs[i] as Blob));
        return { items, vectors, thumbs };
    }

    async thumb(id: string): Promise<Blob | undefined> {
        return request(this.db.transaction("thumbs").objectStore("thumbs").get(id));
    }

    async count(): Promise<number> {
        return request(this.db.transaction("items").objectStore("items").count());
    }

    async remove(id: string): Promise<void> {
        const tx = this.db.transaction(["items", "vectors", "thumbs"], "readwrite");
        for (const store of ["items", "vectors", "thumbs"]) tx.objectStore(store).delete(id);
        await done(tx);
    }

    async clear(): Promise<void> {
        const tx = this.db.transaction(["items", "vectors", "thumbs"], "readwrite");
        for (const store of ["items", "vectors", "thumbs"]) tx.objectStore(store).clear();
        await done(tx);
    }
}
