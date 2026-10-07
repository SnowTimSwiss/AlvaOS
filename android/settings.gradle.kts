// The AlvaOS app for phones (docs/PHOTOS.md). Android first.
//   :core  the sync with the NAS, plain Kotlin: built and tested anywhere
//   :app   the Android app around it: needs the Android SDK (CI has it)
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "AlvaOS"
include(":core")

val sdk = System.getenv("ANDROID_HOME") ?: System.getenv("ANDROID_SDK_ROOT")
    ?: file("local.properties").takeIf { it.exists() }?.readLines()
        ?.firstOrNull { it.startsWith("sdk.dir=") }?.substringAfter("=")
if (sdk != null && file(sdk).isDirectory) {
    include(":app")
} else {
    println("No Android SDK here: building only :core.")
}
