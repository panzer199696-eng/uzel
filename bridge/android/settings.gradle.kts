// Настройки сборки Android-моста УЗЕЛ: модуль :core (чистая JVM) и :app (Android).
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
    }
}

rootProject.name = "uzel-android"

include(":app")
include(":core")
