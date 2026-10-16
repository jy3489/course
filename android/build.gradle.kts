plugins {
    id("com.android.application") version "9.4.1" apply false
    id("org.jetbrains.kotlin.android") version "2.2.21" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.2.21" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.2.21" apply false
}

// Documents may be cloud-synced on macOS. Keep generated compiler files off iCloud.
// Linux/VM builds retain Gradle's usual build directories.
val outputRoot = System.getenv("PORTAL_BUILD_DIR") ?: if (System.getProperty("os.name").contains("Mac"))
    "${System.getProperty("java.io.tmpdir")}/playroom-build-${rootDir.absolutePath.hashCode().toUInt()}" else null
if (outputRoot != null) {
    layout.buildDirectory.set(file("$outputRoot/root"))
    subprojects { layout.buildDirectory.set(file("$outputRoot/$name")) }
}
