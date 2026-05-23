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
        display_off --> last_state
        
        observation --> mortal_danger_signal
        observation --> tail_signal
        observation --> statistic
        
        statistic --> observation
        # observation-->poweroff

        state observation {
            last_state --> regular_observe
            last_state --> attentively_observe
            regular_observe 
            attentively_observe
        }

        state tail_alarm {
            tail_signal --> display_spotter 
            # at half - photo with statistic, at half - real time grab
            display_spotter --> spotter_info_usb_save
            spotter_info_usb_save --> all_chunks_usb_save
            # we are predicting change of cars 
            all_chunks_usb_save --> high_quality_obs
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
### BPMN
![Описание процесса](assets/bpmn.png)