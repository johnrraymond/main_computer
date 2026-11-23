'use strict';
// Scramjet v1.1.0 controller expects these object stores at database version 1.
// Its Service Worker does not create them. Initialize the schema *before*
// registering /sw.js so a worker cannot create an empty version-1 database.
// Isolated-origin storage ONLY; no MCEL trusted-origin data is touched.
window.mcelPrepareScramjetDB = async function mcelPrepareScramjetDB() {
  const DATABASE = '$scramjet';
  const STORES = ['config', 'cookies', 'redirectTrackers', 'referrerPolicies', 'publicSuffixList'];
  function timed(request, label, ms = 7500) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(label + ' timed out; close other Scramjet tabs')), ms);
      request.onsuccess = () => {clearTimeout(timer); resolve(request.result);};
      request.onerror = () => {clearTimeout(timer); reject(request.error || new Error(label + ' failed'));};
      request.onblocked = () => {clearTimeout(timer); reject(new Error(label + ' blocked; close other Scramjet tabs'));};
    });
  }
  function openSchema() {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      for (const name of STORES) {
        if (!db.objectStoreNames.contains(name)) db.createObjectStore(name);
      }
    };
    return timed(request, 'Scramjet IndexedDB initialization');
  }
  let db = await openSchema();
  let missing = STORES.filter(name => !db.objectStoreNames.contains(name));
  if (missing.length) {
    const existing = Array.from(db.objectStoreNames);
    db.close();
    if (existing.length) {
      // Partially populated schema can contain user cookies/config. Do NOT
      // delete it or silently modify version-1 storage under that data.
      throw new Error('Scramjet IndexedDB incomplete (' + missing.join(', ') +
        '); existing stores preserved: ' + existing.join(', '));
    }
    // Worker-first startup may have left an *empty* v1 database. With zero
    // stores there is no Scramjet data to destroy. Drop only that empty DB.
    await timed(indexedDB.deleteDatabase(DATABASE), 'Empty Scramjet IndexedDB cleanup');
    db = await openSchema();
    missing = STORES.filter(name => !db.objectStoreNames.contains(name));
    if (missing.length) {
      db.close();
      throw new Error('Scramjet schema initialization failed: ' + missing.join(', '));
    }
    console.info('[MCEL Scramjet] repaired empty version-1 IndexedDB');
  }
  db.close();
};
