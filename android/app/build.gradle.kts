plugins {
    id("com.android.application") version "8.11.1"
    kotlin("android") version "2.0.21"
}

// The version name is the one of AlvaOS (the VERSION file); the version code
// counts up with every build CI makes (Google Play needs a bigger one each time).
val alvaosVersion = rootDir.resolve("../VERSION").takeIf { it.exists() }?.readText()?.trim() ?: "dev"
val buildNumber = System.getenv("ALVAOS_VERSION_CODE")?.toIntOrNull() ?: 1

// The upload key for Google Play, only from the environment (CI secrets),
// never in the repository. Without it a release build is not signed.
val keystore = System.getenv("ALVAOS_KEYSTORE")?.let { file(it) }?.takeIf { it.exists() }

android {
    namespace = "org.alvaos.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "org.alvaos.app"
        minSdk = 30            // Android 11: deleting pictures through MediaStore's own request
        targetSdk = 36         // what Google Play asks of new apps and updates
        versionCode = buildNumber
        versionName = alvaosVersion
    }
    signingConfigs {
        if (keystore != null) {
            create("upload") {
                storeFile = keystore
                storePassword = System.getenv("ALVAOS_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ALVAOS_KEY_ALIAS")
                keyPassword = System.getenv("ALVAOS_KEY_PASSWORD")
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystore != null) signingConfig = signingConfigs.getByName("upload")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    lint { abortOnError = false }
}

dependencies {
    implementation(project(":core"))
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}
