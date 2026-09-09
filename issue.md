# Rencana High-Level: Adaptive Trade Management

## Latar Belakang
Tujuan utama project ini bukan mengejar target yang terlalu jauh, tetapi menjaga konsistensi profit dan mengurangi kasus posisi yang sempat hijau lalu berbalik menjadi loss. Dari perilaku bot saat ini, masalah utamanya bukan hanya pada kualitas entry, tetapi pada manajemen posisi setelah entry yang masih terlalu statis.

Saat ini sebagian besar trade masih bergantung pada pola sederhana: entry, tunggu ke TP atau SL, lalu baru ada tindakan lanjutan. Pendekatan ini kurang cocok untuk timeframe cepat seperti `15m`, karena market sering memberi profit sementara lalu melakukan reversal sebelum target penuh tercapai.

## Masalah Inti

### 1. Profit belum cukup cepat diamankan
Posisi yang sudah bergerak searah masih dibiarkan terlalu lama tanpa proteksi profit yang bertahap. Akibatnya, floating profit mudah kembali habis saat momentum melemah.

### 2. Manajemen `15m` dan `1h` masih terlalu disamakan
Timeframe mikro memiliki noise dan wick yang jauh lebih tinggi. Karena itu, aturan exit dan proteksi untuk `15m` tidak boleh identik dengan `1h`.

### 3. SL dan TP belum cukup adaptif terhadap candle terbaru
Bot sudah memiliki update SL berkala, tetapi cakupannya masih terbatas. Sistem belum benar-benar membaca struktur candle terbaru untuk memutuskan apakah SL perlu dirapatkan, TP perlu didekatkan, atau posisi perlu ditutup lebih awal.

## Arah Solusi

### 1. Bangun lapisan `adaptive trade management`
Tambahkan layer manajemen posisi yang aktif setelah entry, bukan hanya saat signal dibuat. Layer ini bertugas mengevaluasi perkembangan trade secara berkala dan memperbarui exit sesuai kondisi market terbaru.

### 2. Gunakan manajemen berbasis timeframe asal
Trade `15m` harus dikelola dengan struktur `15m`, trade `1h` dengan struktur `1h`, dan seterusnya. Tujuannya agar keputusan exit tidak terlalu dipengaruhi noise dari timeframe yang tidak relevan.

### 3. Terapkan profit protection bertahap
Bot perlu memiliki beberapa level proteksi, misalnya:
- saat progress menuju target masih awal, SL mulai dirapatkan ringan
- saat progress sudah cukup jauh, SL pindah ke BEP
- saat progress sudah kuat tetapi muncul rejection, profit dikunci atau posisi ditutup parsial

### 4. Tambahkan adaptive exit berbasis candle dan momentum
Jika market menunjukkan sinyal pelemahan, rejection, breakout gagal, atau hilangnya follow-through, bot tidak harus menunggu TP penuh. Dalam kondisi seperti itu, sistem harus dapat memilih antara:
- mendekatkan TP
- menggeser SL lebih agresif
- menutup sebagian posisi
- menutup posisi penuh lebih awal

### 5. Pisahkan profil strategi `15m` dan `1h`
Mode `15m` perlu dibuat lebih defensif: lebih cepat BEP, lebih cepat mengunci profit, dan lebih sensitif terhadap reversal. Mode `1h` dapat diberi ruang lebih luas karena struktur trennya lebih stabil.

## Tahapan Implementasi

### Fase 1
Rapikan aturan dasar manajemen posisi: definisi progress trade, aturan BEP, dan aturan profit lock bertahap.

### Fase 2
Tambahkan evaluasi candle berkala berbasis timeframe asal untuk memperbarui SL dan TP secara adaptif.

### Fase 3
Tambahkan exit parsial dan early-exit saat momentum melemah atau reversal terdeteksi.

### Fase 4
Kalibrasi terpisah untuk `15m` dan `1h`, lalu evaluasi hasilnya dari trade log dan statistik win/loss.

## Hasil yang Diharapkan
Setelah rencana ini diterapkan, bot diharapkan tidak lagi terlalu sering membiarkan floating profit kembali menjadi loss, lebih tahan terhadap wick pada timeframe kecil, dan lebih realistis dalam mengambil profit dari pergerakan market yang benar-benar terjadi.
