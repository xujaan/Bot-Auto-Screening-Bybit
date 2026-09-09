# Technical Plan: Adaptive Trade Management

Dokumen ini adalah rencana implementasi teknis yang cukup detail untuk dikerjakan oleh junior programmer atau model AI eksekutor. Fokus utamanya adalah mengubah manajemen posisi dari statis menjadi adaptif, agar floating profit lebih cepat diamankan dan trade `15m` tidak terlalu mudah berakhir kena reverse atau wick.

## 1. Tujuan Implementasi

### Target bisnis
- kurangi kasus posisi sempat profit lalu berbalik menjadi loss
- kurangi stop kena wick pada `15m`
- buat perilaku `15m` dan `1h` berbeda, tidak lagi disamakan
- buat exit lebih realistis terhadap kondisi candle terbaru

### Target teknis
- tambahkan layer `adaptive trade management` setelah entry
- simpan metadata trade yang cukup untuk evaluasi berkala
- update `SL`, `TP`, atau lakukan close parsial/full berdasarkan progress trade dan struktur candle terbaru

## 2. File yang Akan Diubah

### File utama
- `auto_trades.py`
- `modules/database.py`
- `modules/technicals.py`
- `modules/telegram_listener.py` jika ingin menambah status/debug command

### File opsional
- `dashboard.py` jika ingin menampilkan state adaptive management
- `config.example.json` dan `config.json` untuk parameter baru

## 3. Masalah pada Implementasi Saat Ini

### A. Trade management terlalu pasif
Saat posisi sudah `OPEN_TPS_SET`, bot umumnya hanya:
- menunggu TP tersentuh
- memindah SL ke BEP saat progress 50% ke TP1
- menjalankan trailing hanya untuk mode `NORMAL`

Akibatnya profit yang sudah muncul belum cukup cepat diamankan.

### B. `15m` dan `1h` masih diperlakukan sama
Keduanya sama-sama masuk `SCALPING`, padahal karakter wick, noise, dan kecepatan reversal berbeda.

### C. Periodic update masih terbatas
`run_periodic_sl_update()` hanya:
- jalan tiap 15 menit
- hanya untuk `SCALPING`
- hanya update `SL`
- selalu fetch candle `15m`

Ini belum cukup untuk adaptive exit.

## 4. Desain Baru yang Diinginkan

### Konsep inti
Setelah trade aktif, bot harus punya loop evaluasi yang menjawab pertanyaan ini:
- seberapa jauh progress trade ke target?
- apakah struktur candle masih mendukung arah posisi?
- apakah perlu menaikkan proteksi profit?
- apakah perlu mendekatkan target?
- apakah perlu keluar lebih awal?

### Prinsip utama
- gunakan timeframe asal trade sebagai basis evaluasi
- proteksi profit dilakukan bertahap
- jangan pernah melonggarkan SL; hanya boleh tetap atau makin ketat
- adaptive exit harus bisa bekerja untuk `SCALPING` juga, bukan hanya `NORMAL`

## 5. Perubahan Data dan Schema

### Tambah kolom pada `active_trades`
Tambahkan kolom berikut jika belum ada:
- `origin_timeframe` VARCHAR(5)
- `management_state` VARCHAR(30)
- `progress_ratio` DECIMAL
- `peak_price` DECIMAL
- `locked_profit_level` INT DEFAULT 0
- `last_candle_check_at` TIMESTAMP
- `last_sl_update_at` TIMESTAMP
- `last_tp_update_at` TIMESTAMP
- `last_management_note` TEXT
- `partial_tp_done` BOOLEAN DEFAULT FALSE
- `early_exit_done` BOOLEAN DEFAULT FALSE

### Nilai awal yang disarankan
- `management_state = 'LIVE_MONITORING'`
- `progress_ratio = 0`
- `peak_price = entry_price`
- `locked_profit_level = 0`

### Catatan
Kalau tidak ingin migrasi rumit, boleh mulai dari subset minimum:
- `origin_timeframe`
- `progress_ratio`
- `peak_price`
- `locked_profit_level`
- `last_management_note`

## 6. Config Baru yang Perlu Ditambah

Tambahkan blok baru, misalnya `adaptive_management`:

```json
"adaptive_management": {
  "enabled": true,
  "check_interval_seconds": 30,
  "use_origin_timeframe": true,
  "candle_fetch_limit": 60,
  "bep_trigger_ratio_15m": 0.30,
  "bep_trigger_ratio_1h": 0.45,
  "profit_lock_ratio_1": 0.25,
  "profit_lock_ratio_2": 0.50,
  "profit_lock_ratio_3": 0.75,
  "tp_shrink_on_rejection": true,
  "allow_partial_close": true,
  "partial_close_ratio": 0.50,
  "max_stagnant_candles_15m": 4,
  "max_stagnant_candles_1h": 3
}
```

Parameter ini belum final. Fokus awal adalah memberi tempat konfigurasi yang jelas.

## 7. Aturan Logika yang Harus Dibuat

### A. Hitung progress trade
Buat helper baru, misalnya:
- `calculate_trade_progress(entry, current_price, tp1, side)`

Rumus:
- Long: `(current_price - entry) / (tp1 - entry)`
- Short: `(entry - current_price) / (entry - tp1)`

Hasil:
- `< 0` berarti posisi sedang rugi
- `0.0 - 1.0` berarti masih dalam perjalanan ke TP1
- `>= 1.0` berarti TP1 sudah seharusnya tercapai atau terlampaui

Nilai ini harus disimpan ke `progress_ratio`.

### B. Track peak price
Simpan harga terbaik sejak entry:
- Long: peak = harga tertinggi yang tercatat setelah posisi aktif
- Short: peak = harga terendah yang tercatat setelah posisi aktif

Peak ini dipakai untuk mendeteksi reversal dari kondisi yang sempat profit.

### C. Profit lock ladder
Buat 3 level proteksi minimum:

#### Level 1
Trigger:
- `15m`: progress >= `0.25`
- `1h`: progress >= `0.30`

Aksi:
- rapatkan SL sedikit ke arah entry
- jangan langsung BEP, beri ruang kecil agar tidak terlalu mudah tersapu
- update `locked_profit_level = 1`

#### Level 2
Trigger:
- `15m`: progress >= `0.50`
- `1h`: progress >= `0.60`

Aksi:
- pindah SL ke BEP atau sedikit profit
- update `locked_profit_level = 2`

#### Level 3
Trigger:
- `15m`: progress >= `0.75`
- `1h`: progress >= `0.80`

Aksi:
- SL dipindah ke area profit terproteksi
- jika ada sinyal rejection, izinkan partial close atau TP didekatkan
- update `locked_profit_level = 3`

### D. Rejection and momentum check
Buat helper baru, misalnya:
- `detect_rejection_signal(df, side)`
- `detect_momentum_loss(df, side)`

Versi awal tidak perlu terlalu kompleks. Cukup pakai aturan sederhana:

Untuk long:
- candle terakhir punya upper wick besar dan close lemah
- 2 candle terakhir membuat high baru tetapi gagal close kuat
- close candle terakhir turun di bawah mid-body candle sebelumnya

Untuk short:
- kebalikan dari aturan long

Output:
- `is_rejection = True/False`
- `reason = 'upper_wick_rejection'`, `double_failed_breakout`, dll

### E. Stagnation / no follow-through check
Buat helper:
- `detect_stagnation(df, entry, current_price, side, origin_tf)`

Aturan awal:
- jika trade `15m` sudah berjalan `4` candle dan progress masih kecil, anggap follow-through lemah
- jika trade `1h` sudah berjalan `3` candle dan progress masih kecil, anggap lambat

Aksi yang bisa dipicu:
- rapatkan SL
- dekatkan TP
- atau keluar lebih awal jika ada rejection tambahan

### F. TP shrink rule
Kalau trade sudah sempat profit cukup baik tetapi muncul rejection, bot boleh mendekatkan target.

Contoh:
- TP1 awal = 1.5%
- progress sudah 0.70
- muncul rejection kuat
- ubah target aktif menjadi 1.1% atau close posisi parsial

Catatan:
- jangan sering-sering mengubah TP
- hanya boleh dilakukan jika `locked_profit_level >= 2`

### G. Early exit rule
Tutup posisi lebih awal jika semua syarat ini terpenuhi:
- trade sempat profit
- terjadi rejection atau momentum loss
- progress turun signifikan dari peak progress

Contoh aturan awal:
- peak progress pernah >= `0.60`
- sekarang turun ke <= `0.35`
- ada rejection signal

Aksi:
- close market sebagian atau penuh
- tulis alasan ke `last_management_note`

## 8. Perubahan Fungsi yang Harus Dibuat

### Di `modules/technicals.py`
Tambahkan helper baru:
- `calculate_trade_progress(...)`
- `detect_rejection_signal(df, side)`
- `detect_momentum_loss(df, side)`
- `get_recent_structure(df, side)` jika diperlukan untuk swing high/low terbaru

Fungsi harus sederhana, deterministic, dan mudah diuji.

### Di `auto_trades.py`
Tambahkan fungsi baru:
- `fetch_management_candles(exchange, symbol, timeframe, limit=60)`
- `evaluate_trade_management(trade_row, pos, df)`
- `apply_profit_lock(exchange, trade_row, pos, new_sl, note)`
- `apply_tp_adjustment(exchange, trade_row, pos, new_tp, note)`
- `execute_partial_close(exchange, trade_row, pos, close_ratio, note)`
- `execute_early_exit(exchange, trade_row, pos, note)`

### Refactor fungsi yang sudah ada
Refactor `run_periodic_sl_update()` menjadi worker yang lebih umum, misalnya:
- nama baru: `run_adaptive_trade_management()`

Fungsi baru ini harus:
- ambil semua trade `OPEN` dan `OPEN_TPS_SET`
- gunakan `origin_timeframe`, bukan hardcoded `15m`
- fetch candle terbaru
- hitung progress
- update peak price
- jalankan profit lock ladder
- cek rejection / stagnation
- putuskan update `SL`, `TP`, partial close, atau early exit

## 9. Urutan Implementasi yang Disarankan

### Fase 1: Fondasi data dan helper
- tambah config `adaptive_management`
- tambah kolom minimum di `active_trades`
- simpan `origin_timeframe` saat ingest signal
- buat helper progress dan peak tracking

Deliverable:
- trade punya metadata minimum untuk dimonitor

### Fase 2: Profit lock ladder
- implementasikan Level 1, 2, 3
- update `SL` berdasarkan progress
- simpan `locked_profit_level`

Deliverable:
- posisi yang sempat hijau tidak lagi dibiarkan tanpa proteksi

### Fase 3: Timeframe-aware management
- gunakan timeframe asal trade untuk fetch candle evaluasi
- hapus hardcode `15m` dari periodic manager
- buat parameter berbeda untuk `15m` dan `1h`

Deliverable:
- `15m` dan `1h` punya perilaku manajemen berbeda

### Fase 4: Rejection and stagnation logic
- implementasikan helper rejection
- implementasikan helper no follow-through
- rapatkan SL atau dekatkan TP saat sinyal ini muncul

Deliverable:
- trade bisa bereaksi saat momentum melemah

### Fase 5: Partial close dan early exit
- implement partial close via `reduceOnly`
- implement early exit market close
- simpan alasan exit ke DB/log

Deliverable:
- profit yang sudah ada bisa diamankan sebelum reversal total

## 10. Detail Implementasi per Step

### Step 1
Saat `ingest_fresh_signals()` memasukkan trade baru ke `active_trades`, simpan:
- `origin_timeframe = tf`
- `management_state = 'LIVE_MONITORING'`
- `progress_ratio = 0`
- `peak_price = entry`
- `locked_profit_level = 0`

### Step 2
Ganti `run_periodic_sl_update()` menjadi `run_adaptive_trade_management()`.

Frekuensi awal yang disarankan:
- scheduler setiap `30` detik atau `60` detik
- bukan setiap 15 menit

Catatan:
- evaluasi candle tetap berdasarkan timeframe asal
- polling manager boleh lebih sering karena keputusan tidak harus menunggu close candle penuh untuk semua rule

### Step 3
Di awal evaluasi tiap trade:
- fetch posisi dari exchange
- skip jika posisi sudah closed
- fetch candles dengan timeframe asal
- hitung `progress_ratio`
- update `peak_price`

### Step 4
Jalankan ladder:
- jika level 1 belum pernah aktif dan syarat tercapai, update SL
- jika level 2 belum pernah aktif dan syarat tercapai, update SL ke BEP/profit kecil
- jika level 3 tercapai, aktifkan mode proteksi agresif

### Step 5
Jika `locked_profit_level >= 2`, cek:
- rejection
- momentum loss
- stagnation

Jika salah satu kuat:
- rapatkan SL
- atau dekatkan TP

Jika kombinasi sinyal kuat:
- partial close
- atau early exit penuh

### Step 6
Setiap aksi harus:
- update DB
- update note/log
- hindari spam order berulang

Tambahkan guard:
- jangan update SL/TP jika perubahan terlalu kecil
- jangan close parsial dua kali jika `partial_tp_done = True`

## 11. Logging yang Wajib Ada

Setiap keputusan adaptive management harus menulis log yang jelas:
- symbol
- timeframe asal
- progress ratio
- peak price
- locked profit level
- action yang diambil
- alasan action

Contoh:
- `BTC/USDT 15m progress=0.62 level=2 action=move_sl_to_bep reason=profit_lock_level_2`
- `ETH/USDT 15m progress=0.74 action=partial_close reason=upper_wick_rejection_near_tp`

## 12. Kriteria Selesai

Task dianggap selesai jika:
- trade menyimpan timeframe asal dan metadata management
- periodic manager tidak lagi hardcoded ke `15m`
- ada minimal 3 level profit lock
- ada minimal 1 rule rejection
- ada minimal 1 rule stagnation
- ada partial close atau early exit
- semua aksi tercatat jelas di log dan DB

## 13. Hal yang Jangan Dilakukan

- jangan ubah terlalu banyak strategi entry dalam task ini
- jangan gabungkan refactor besar lain yang tidak terkait
- jangan membuat sinyal rejection terlalu rumit di versi pertama
- jangan menambah banyak indikator baru kalau belum benar-benar perlu
- jangan melonggarkan SL; adaptasi hanya boleh membuat risiko tetap atau lebih kecil

## 14. Saran Urutan Kerja untuk Junior / AI Executor

1. Tambah schema dan config dulu.
2. Simpan metadata trade saat ingest.
3. Buat helper progress, peak, rejection, stagnation.
4. Refactor periodic SL updater menjadi adaptive manager umum.
5. Implement profit lock ladder.
6. Tambahkan TP adjustment.
7. Tambahkan partial close dan early exit.
8. Tambahkan log dan validasi anti-duplicate action.
9. Uji manual di mode paper/safe config.

## 15. Validasi Manual Setelah Implementasi

Skenario minimal yang harus dites:
- trade `15m` profit 30% menuju target lalu reverse
- trade `15m` profit 70% lalu muncul wick rejection
- trade `1h` bergerak lambat tetapi tetap searah
- trade sempat kena level 2 lalu balik ke entry
- trade yang stagnan selama beberapa candle

Hasil yang diharapkan:
- `15m` lebih cepat mengunci profit
- `1h` tidak terlalu sensitif terhadap noise kecil
- posisi yang sempat hijau tidak terlalu sering berakhir full loss
