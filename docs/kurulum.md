# Kurulum (BT öğretmeni için)

Rayyan Ekitap, Pardus ETAP akıllı tahtalara USB bellekten kurulur. Yönetici
(`etapadmin`) parolası gerekmez; kurulum, oturum açmış öğretmenin ev dizinine
yapılır.

## Gerekenler

- Pardus ETAP 23.4 veya 25 kurulu bir tahta.
- Üzerinde `INTERAKTIV` etiketiyle kurulum paketi bulunan bir USB bellek.
  Paket, kitaplarla birlikte yaklaşık 150 MB ile birkaç GB arasındadır.

## Adımlar

1. Tahtada kullanılacak öğretmen hesabıyla oturum açın (`ogretmen` ya da
   kişisel hesap).
2. Belleği takın. Tahta, *"Bu ortam kendiliğinden çalıştırılması istenen bir
   yazılım içeriyor. Çalıştırmak ister misiniz?"* diye sorar. **Çalıştır**'a
   dokunun.
3. Bir dakika kadar süren kurulum bitince Rayyan Ekitap açılır. Bundan sonra
   Pardus menüsünde **Rayyan Ekitap** adıyla bulunur ve bellek olmadan çalışır.

Soru çıkmazsa belleği Dosyalar'da açın, boş bir yere sağ tıklayın (parmağınızı
basılı tutun), **Uçbirimde Aç**'ı seçin ve şunu yazın:

```bash
sh autorun.sh
```

Her tahta hesabı ayrıdır: başka bir öğretmen hesabında da kullanılacaksa
belleği o hesapta bir kez daha takın.

## Kurulumdan sonra

- Kurulum yeri: `~/.local/share/interaktiv`. Sistem dizinlerine hiçbir şey
  yazılmaz.
- İndirilen kitaplar `~/.local/share/interaktiv/app/books` altındadır ve
  güncelleme sırasında korunur.
- Bellekteki `results` klasörüne her kurulumda tahta hakkında kısa bir rapor
  yazılır (`rapor-<tarih>.txt`). Bir sorun olursa bu klasörü geliştiriciye
  iletin.
- Kaldırmak için: `sh ~/.local/share/interaktiv/kaldir.sh`

## Öğrenci hesabı

`ogrenci` hesabında ETA Sınırlı Erişim uygulamaları kısıtlar. Öğrencilerin
kullanması isteniyorsa Rayyan Ekitap o hesapta kurulmalı ve Sınırlı Erişim'de
izin verilmelidir.

## Sorun giderme

- **"Rayyan Ekitap başlatılamadı" penceresi.** Pencerenin fotoğrafını çekin;
  ayrıntılar `~/.cache/interaktiv/interaktiv.log` dosyasındadır.
- **Kitap indirilemiyor.** Okul ağı filtreli olabilir; kitaplar
  `ogm-large-cdn.eba.gov.tr` adresinden indirilir. Kitaplar kurulum paketiyle
  birlikte de verilebilir.
- **Etkileşimli etkinlik açılmıyor.** Bu etkinlikler tarayıcıda (Chrome)
  açılır ve ilk açılışta internet gerektirir.
