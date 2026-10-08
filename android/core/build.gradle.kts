plugins {
    kotlin("jvm") version "2.0.21"
    kotlin("plugin.serialization") version "2.0.21"
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}
kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    api("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    // AlvaOS Link on the phone (docs/LINK.md): iroh, end to end encrypted connections to the NAS.
    // Its Kotlin bindings are in src/main/kotlin/computer/iroh (generated, see the README there); they
    // call the native library through JNA. Here (tests) JNA is the plain jar, in the app its aar.
    compileOnly("net.java.dev.jna:jna:5.15.0")
    testImplementation("net.java.dev.jna:jna:5.15.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0")
    testImplementation(kotlin("test"))
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
}

tasks.test {
    useJUnitPlatform()
    // The reason of a failure in the CI log, not only "IOException at line 94".
    testLogging {
        events("failed")
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}

// The tests of AlvaOS Link use the real iroh. Its desktop libraries come out of the published JVM package
// (computer.iroh:iroh), fetched once into build/. The phone gets its own library built by CI (docs/LINK.md).
val irohJar = layout.buildDirectory.file("iroh/iroh-1.1.0.jar")
val fetchIroh = tasks.register("fetchIroh") {
    outputs.file(irohJar)
    doLast {
        val file = irohJar.get().asFile
        if (!file.exists()) {
            file.parentFile.mkdirs()
            ant.invokeMethod("get", mapOf("src" to "https://repo1.maven.org/maven2/computer/iroh/iroh/1.1.0/iroh-1.1.0.jar",
                "dest" to file))
        }
    }
}
val irohNative = tasks.register<Copy>("irohNative") {
    dependsOn(fetchIroh)
    from({ zipTree(irohJar.get().asFile) }) { include("linux-x86-64/**", "linux-aarch64/**", "darwin-aarch64/**", "win32-x86-64/**") }
    into(layout.buildDirectory.dir("iroh-native"))
}
sourceSets.test { resources.srcDir(layout.buildDirectory.dir("iroh-native")) }
tasks.processTestResources { dependsOn(irohNative) }
