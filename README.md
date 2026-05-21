# Пет-проект по машинному зрению
### Описание
Не завезли
### Диаграмма состояний проекта
Нужно:
* Реализовать `observation`
* Вынести `high_quality_obs` в `work`, чтобы на следующий запуск после обнаружения хвоста камера искала более внимательно
* Переопределить `statistics`
```mermaid
stateDiagram
    [*] --> autorun_script
    poweroff --> [*]
    kill_sys --> [*]

    state Work {
        autorun_script --> display_off
        display_off --> observation
        
        observation --> mortal_danger_signal
        observation --> tail_signal
        observation --> statistic
        
        statistic --> observation
        # observation-->poweroff
    
        state tail_alarm {
            tail_signal --> display_spotter 
            # at half - photo with statistic, at half - real time grab
            display_spotter --> archiving_all_chunks
            archiving_all_chunks --> high_quality_obs
            # permanent saving
            high_quality_obs --> poweroff
        }

        state enemies_alarm {
            mortal_danger_signal --> kill_sys
            kill_sys
        }

        state statistic {
            nothing_strange
            anomaly
            state anomaly {
                many_ghost_riders

            }
            potential_list
            debug_full_info
        }
        
        poweroff
    }
```