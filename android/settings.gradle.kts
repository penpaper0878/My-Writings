pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        maven("https://maven.ghostscript.com") // MuPDF
    }
}

rootProject.name = "pdf2md-android"

// The Markdown engine is plain Kotlin: its own build, so it builds and tests
// anywhere (`./gradlew -p core test`), even without the Android SDK.
includeBuild("core")
include(":app")
