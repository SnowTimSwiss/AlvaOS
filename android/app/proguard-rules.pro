# R8 rules of the AlvaOS app.

# AlvaOS Link: the iroh bindings call the native library through JNA, by names.
-keep class com.sun.jna.** { *; }
-keep class * implements com.sun.jna.** { *; }
-keep class computer.iroh.** { *; }
-dontwarn java.awt.**
-dontwarn com.sun.jna.**

# The Hub page calls the app through this object (AlvaApp in JavaScript).
-keepclassmembers class org.alvaos.app.HubPage$Bridge {
    @android.webkit.JavascriptInterface <methods>;
}

# kotlinx.serialization: the generated serializers are found by name.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class kotlinx.serialization.json.** { *** Companion; }
-keepclasseswithmembers class kotlinx.serialization.json.** { kotlinx.serialization.KSerializer serializer(...); }
-keep,includedescriptorclasses class org.alvaos.photos.**$$serializer { *; }
-keepclassmembers class org.alvaos.photos.** {
    *** Companion;
}
-keepclasseswithmembers class org.alvaos.photos.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# Android makes these from the manifest and WorkManager makes the worker from its name.
-keep class org.alvaos.app.SyncWorker { *; }

# OkHttp (optional TLS providers it looks for)
-dontwarn org.bouncycastle.**
-dontwarn org.conscrypt.**
-dontwarn org.openjsse.**
