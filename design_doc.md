# Model 
----

## Job:
    - id 
    - name of each job 
    - status of the job 
    - conclusion- its different from status cause status tells us where in the lifecycle the job is is it currently running in progress or not 
        - a conclusion is what happenes after the job finsihes sucess failure skipped or cancled 
    - started_at - what time the job started in seconds 
    - duration_s - How long it took the job to finish in seconds 

## Run
    - id 
    - gh_run_id - im assuming github doesn't have its own run id, so we assign it one 
    - workflow-id - The id of the workflow 
    - created at: a string that tells us when the run was created 
    - job - a list of all [Jobs]

## Workflow:
    - id
    - filename 
    - raw_yaml

## RepoProfile:
    - id  
    - owner
    - name of repo 
    - workflow : list[Workflow]
    - runs: List[Run]
        - if I wanted to acess anything within repo Profile i would do profile.runs[0].jobs[0] this would give me the first job 





 
